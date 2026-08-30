"""A workbook's structure, written into the property graph.

Nothing here is extracted -- every node and edge is derived from what the
parser already read out of the file, so the spreadsheet's shape in the graph is
exactly right rather than as right as a model happened to be:

    (:Workbook) -[:HAS_SHEET]-> (:Worksheet) -[:HAS_COLUMN]-> (:Column)
    (:Column)   -[:DERIVED_FROM]-> (:Column)     from the captured formula
    (:Column)   -[:HAS_VALUE]->  (existing entity)  a cell value a document
                                                    already talks about

Rows never enter the graph -- values live in DuckDB, which is what answers
questions about them. Each node's description doubles as its retrieval card in
the entity vector store, so a question naming a column or a worksheet lands on
it exactly (that is the sparse half of hybrid search) and the SQL writer can be
handed only the tables that were actually retrieved.
"""

import logging

from app.core.config import get_settings
from app.services import graph_schema as gs
from app.services.parsers.spreadsheet import get_connection

logger = logging.getLogger("app.tabular_graph")
_settings = get_settings()


def workbook_node(file_name: str) -> str:
    return file_name


def worksheet_node(file_name: str, worksheet: str) -> str:
    return f"{file_name}:{worksheet}"


def column_node(file_name: str, worksheet: str, column: str) -> str:
    return f"{file_name}:{worksheet}.{column}"


def _column_card(sheet: dict, column: dict, file_name: str) -> str:
    card = (
        f"Column {column['name']} (header {column['header']!r}, "
        f"{column['semantic']}, {column['data_type']}) of worksheet "
        f"{sheet['worksheet']!r} in the spreadsheet {file_name}, queryable as "
        f"{sheet['table']}.{column['name']}."
    )
    if column["derived_from"]:
        card += (
            f" Derived from {' and '.join(column['derived_from'])} via the "
            f"formula {column['formula']}."
        )
    elif column["formula"]:
        card += f" Computed by the formula {column['formula']}."
    return card


async def _link_known_values(rag, sheet: dict, column: dict, file_name: str, doc_id: str):
    """Join a categorical column to entities the documents already named.

    Only values that are *already* nodes get an edge: a spreadsheet must not
    invent thousands of one-off nodes, and a value nothing else mentions has
    nothing to connect to.

    ponytail: one-directional and order-dependent -- a contract ingested after
    its spreadsheet won't retro-link. Re-run project() on the workbook (or
    re-upload it) to pick those up.
    """
    con = get_connection()
    rows = con.execute(
        f'SELECT DISTINCT "{column["name"]}" FROM "{sheet["table"]}" '
        f'WHERE "{column["name"]}" IS NOT NULL '
        f"LIMIT {int(_settings.spreadsheet_max_graph_values)}"
    ).fetchall()

    node = column_node(file_name, sheet["worksheet"], column["name"])
    for (value,) in rows:
        # Documents store entities under the canonical name, so the raw cell
        # "Acme Corp" has to be folded the same way to find the ACME CORP node
        # a contract created -- otherwise the link silently never happens.
        value = str(value).strip()
        entity = gs.canonical_name(value)
        if not entity or await rag.chunk_entity_relation_graph.get_node(entity) is None:
            continue
        await gs.upsert_edge(
            rag,
            node,
            entity,
            gs.HAS_VALUE,
            description=(
                f"{value!r} appears in column {column['name']} of "
                f"{sheet['table']}."
            ),
            file_path=file_name,
            source_id=doc_id,
        )


async def project(rag, workbook: dict, doc_id: str) -> None:
    """Write (or rewrite) the whole workbook's structure. Idempotent."""
    file_name = workbook["file_name"]
    sheets = workbook["sheets"]
    total_rows = sum(sheet["rows"] for sheet in sheets)

    wb_node = workbook_node(file_name)
    await gs.upsert_node(
        rag,
        wb_node,
        gs.WORKBOOK,
        description=(
            f"Spreadsheet workbook {file_name} with {len(sheets)} worksheet(s) "
            f"and {total_rows} rows in total: "
            f"{', '.join(sheet['worksheet'] for sheet in sheets)}."
        ),
        file_path=file_name,
        source_id=doc_id,
    )

    for sheet in sheets:
        ws_node = worksheet_node(file_name, sheet["worksheet"])
        await gs.upsert_node(
            rag,
            ws_node,
            gs.WORKSHEET,
            description=(
                f"Worksheet {sheet['worksheet']!r} of the spreadsheet "
                f"{file_name}, queryable as the table {sheet['table']} with "
                f"{sheet['rows']} rows. Columns: "
                f"{', '.join(c['name'] for c in sheet['columns'])}."
            ),
            file_path=file_name,
            source_id=doc_id,
            table=sheet["table"],
        )
        await gs.upsert_edge(
            rag,
            wb_node,
            ws_node,
            gs.HAS_SHEET,
            description=f"{file_name} contains the worksheet {sheet['worksheet']!r}.",
            file_path=file_name,
            source_id=doc_id,
        )

        by_header = {c["header"]: c["name"] for c in sheet["columns"]}
        for column in sheet["columns"]:
            col_node = column_node(file_name, sheet["worksheet"], column["name"])
            await gs.upsert_node(
                rag,
                col_node,
                gs.COLUMN,
                description=_column_card(sheet, column, file_name),
                file_path=file_name,
                source_id=doc_id,
                table=sheet["table"],
                column=column["name"],
            )
            await gs.upsert_edge(
                rag,
                ws_node,
                col_node,
                gs.HAS_COLUMN,
                description=f"{sheet['table']} has the column {column['name']}.",
                file_path=file_name,
                source_id=doc_id,
            )
            for source_header in column["derived_from"]:
                if source_header not in by_header:
                    continue
                await gs.upsert_edge(
                    rag,
                    col_node,
                    column_node(
                        file_name, sheet["worksheet"], by_header[source_header]
                    ),
                    gs.DERIVED_FROM,
                    description=(
                        f"{column['name']} is computed from "
                        f"{by_header[source_header]} by {column['formula']}."
                    ),
                    file_path=file_name,
                    source_id=doc_id,
                )
            if column["semantic"] in ("categorical", "text"):
                await _link_known_values(rag, sheet, column, file_name, doc_id)

    await gs.flush(rag)
    logger.info(
        "Projected %s into the graph: %d worksheet(s), %d column(s)",
        file_name,
        len(sheets),
        sum(len(sheet["columns"]) for sheet in sheets),
    )


async def remove(rag, file_name: str) -> None:
    """Drop a workbook's structural nodes. Must run before the DuckDB tables
    are dropped -- their metadata is what says which nodes exist.

    Cell-value nodes are left alone: they are document entities that a
    spreadsheet merely pointed at, and deleting the workbook does not unsay
    what the document said. Their HAS_VALUE edges go with the column nodes.
    """
    con = get_connection()
    rows = con.execute(
        "SELECT DISTINCT worksheet, column_name FROM _node_rels_columns WHERE workbook = ?",
        [file_name],
    ).fetchall()
    if not rows:
        return

    nodes = [workbook_node(file_name)]
    nodes += sorted({worksheet_node(file_name, worksheet) for worksheet, _ in rows})
    nodes += [column_node(file_name, worksheet, column) for worksheet, column in rows]
    await gs.remove_nodes(rag, nodes)
    await gs.flush(rag)
    logger.info("Removed %d graph node(s) for %s", len(nodes), file_name)
