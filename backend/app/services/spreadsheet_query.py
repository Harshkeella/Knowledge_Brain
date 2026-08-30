"""Natural language -> validated DuckDB SQL -> exact rows.

The LLM only ever writes SQL; DuckDB does every piece of arithmetic. Three
guardrails, in order: the statement must parse as a single SELECT, every name
it references must bind against the real schema, and the result is capped.
"""

import datetime
import decimal
import logging
import re

import duckdb

from app.core.config import get_settings
from app.services.lightrag_engine import query_llm_func
from app.services.parsers.spreadsheet import get_connection, record_columns

logger = logging.getLogger("app.spreadsheet_query")
_settings = get_settings()

_ADD_COLUMN = re.compile(
    r"^ADD\s+COLUMN\s+([\w]+)\.([\w]+)\s*=\s*(.+)$", re.IGNORECASE | re.DOTALL
)
_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$", re.IGNORECASE)

_SYSTEM_PROMPT = """You translate questions into DuckDB SQL over spreadsheet \
tables. Reply with exactly ONE of:

1. A single SELECT statement answering the question.
2. ADD COLUMN <table>.<new_column> = <sql_expression>  -- only when the user \
asks to add or compute a new column.
3. NO_SQL -- when the question is not about the data in these tables.

Use only the tables and columns in the schema below. No prose, no markdown \
fences, no explanation, no trailing semicolon."""


class SpreadsheetError(Exception):
    """Surfaced to the user; never contains raw SQL errors alone."""


def _cell(value):
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def list_tables() -> list[str]:
    con = get_connection()
    return [
        row[0]
        for row in con.execute(
            "SELECT DISTINCT table_name FROM _node_rels_columns ORDER BY table_name"
        ).fetchall()
    ]


async def relevant_tables(rag, question: str) -> list[str]:
    """Which tables the question is actually about, by retrieval rather than by
    asking a model.

    Every worksheet and column is a node in the graph whose description is
    indexed in the entity vector store, so naming a column hits it exactly on
    the sparse half of hybrid search. An empty list means the question is not
    about the tabular data and no SQL call is made at all -- which is the
    common case, and used to cost an LLM round-trip to discover.
    """
    from app.services import graph_schema as gs

    hits = await rag.entities_vdb.query(question, top_k=_settings.rerank_top_n)
    graph = rag.chunk_entity_relation_graph

    tables: list[str] = []
    for hit in hits:
        node = await graph.get_node(hit.get("entity_name", ""))
        if node is None or node.get("entity_type") not in gs.TABULAR_LABELS:
            continue
        table = node.get("table")
        if table and table not in tables:
            tables.append(table)
    return tables


def schema_context(tables: list[str] | None = None) -> str:
    """What the LLM is allowed to reference. Empty string = nothing to query.

    Scoped to `tables` when the router found some, so the prompt stays the same
    size whether one workbook is loaded or fifty.
    """
    con = get_connection()
    where = ""
    params: list = []
    if tables:
        where = f"WHERE table_name IN ({', '.join('?' * len(tables))})"
        params = list(tables)
    rows = con.execute(
        f"""SELECT table_name, workbook, worksheet, column_name, data_type,
                   semantic, derived_from
            FROM _node_rels_columns {where} ORDER BY table_name, rowid""",
        params,
    ).fetchall()
    if not rows:
        return ""

    lines: list[str] = []
    current = None
    for table, workbook, worksheet, column, dtype, semantic, derived in rows:
        if table != current:
            current = table
            lines.append(f'\nTABLE {table}  -- worksheet "{worksheet}" of {workbook}')
        note = f", derived from {derived}" if derived else ""
        lines.append(f"  {column} {dtype}  -- {semantic}{note}")
    return "\n".join(lines).strip()


def _validate_select(sql: str) -> None:
    """Reject anything that isn't one SELECT, and anything naming a column or
    table that doesn't exist -- both before a single row is read.

    DESCRIBE binds the statement against the real catalog without running it,
    so hallucinated names fail here rather than halfway through a scan.
    """
    con = get_connection()
    try:
        statements = con.extract_statements(sql)
    except duckdb.Error as e:
        raise SpreadsheetError(f"Could not parse the generated SQL: {e}") from e

    if len(statements) != 1:
        raise SpreadsheetError("Only a single statement is allowed.")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise SpreadsheetError(
            f"Only SELECT queries are allowed here (got {statements[0].type.name})."
        )
    if "_node_rels_columns" in sql.lower():
        raise SpreadsheetError("The internal metadata table is not queryable.")

    try:
        con.execute(f"DESCRIBE {sql}")
    except duckdb.Error as e:
        raise SpreadsheetError(f"Query references something that does not exist: {e}") from e


def run_select(sql: str) -> dict:
    """Validate, then execute with a hard row cap."""
    _validate_select(sql)
    con = get_connection()
    limit = _settings.spreadsheet_max_rows
    cursor = con.execute(f"SELECT * FROM ({sql}) LIMIT {limit + 1}")
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {
        "columns": columns,
        "rows": [[_cell(v) for v in row] for row in rows],
        "total_row_count": len(rows),
        "truncated": truncated,
        "sql": sql,
    }


def add_computed_column(table: str, column: str, expression: str) -> dict:
    """The write path: never general SQL, only ADD COLUMN + UPDATE with an
    expression the binder has already checked against the real columns."""
    con = get_connection()
    if table not in list_tables():
        raise SpreadsheetError(f"Unknown table: {table}")
    column = re.sub(r"\W+", "_", column).strip("_").lower()
    if not column:
        raise SpreadsheetError("Invalid column name.")

    existing = [
        row[0] for row in con.execute(f'DESCRIBE "{table}"').fetchall()
    ]
    if column in existing:
        raise SpreadsheetError(f"Column {column!r} already exists in {table}.")

    # Binds the expression against the real columns and tells us its type.
    try:
        described = con.execute(
            f'DESCRIBE SELECT ({expression}) AS {column} FROM "{table}"'
        ).fetchall()
    except duckdb.Error as e:
        raise SpreadsheetError(f"Invalid column expression: {e}") from e
    data_type = described[0][1]

    con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {data_type}')
    con.execute(f'UPDATE "{table}" SET "{column}" = ({expression})')

    workbook, worksheet = con.execute(
        "SELECT workbook, worksheet FROM _node_rels_columns WHERE table_name = ? LIMIT 1",
        [table],
    ).fetchone()
    derived_from = [name for name in existing if re.search(rf"\b{re.escape(name)}\b", expression)]
    record_columns(
        con,
        table,
        [
            {
                "name": column,
                "data_type": data_type,
                "semantic": "derived",
                "formula": expression,
                "derived_from": derived_from,
            }
        ],
        workbook,
        worksheet,
        added_later=True,
    )
    logger.info("Added derived column %s.%s = %s", table, column, expression)

    result = run_select(f'SELECT * FROM "{table}"')
    result["added_column"] = column
    result["table"] = table
    return result


def drop_computed_column(table: str, column: str) -> bool:
    """Undo for add_computed_column. Only columns this feature added can go --
    the workbook's own data is not deletable through here."""
    con = get_connection()
    row = con.execute(
        "SELECT added_later FROM _node_rels_columns WHERE table_name = ? AND column_name = ?",
        [table, column],
    ).fetchone()
    if row is None or not row[0]:
        return False
    con.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
    con.execute(
        "DELETE FROM _node_rels_columns WHERE table_name = ? AND column_name = ?",
        [table, column],
    )
    return True


async def _generate(question: str, schema: str, previous_error: str | None) -> str:
    prompt = f"Schema:\n{schema}\n\nQuestion: {question}"
    if previous_error:
        prompt += (
            f"\n\nYour previous answer failed. Fix it.\n{previous_error}\n"
            "Reply with corrected SQL only."
        )
    answer = await query_llm_func(prompt, system_prompt=_SYSTEM_PROMPT)
    return _FENCE.sub("", str(answer).strip()).strip().rstrip(";")


async def answer(question: str, tables: list[str] | None = None) -> dict | None:
    """Run the question against `tables`. None means "not a spreadsheet
    question" -- the caller falls back to the document RAG path."""
    schema = schema_context(tables)
    if not schema:
        return None

    error: str | None = None
    for attempt in range(3):
        try:
            generated = await _generate(question, schema, error)
        except Exception as e:
            logger.warning("SQL generation call failed: %s", e)
            return None

        if not generated or generated.upper().startswith("NO_SQL"):
            return None

        try:
            match = _ADD_COLUMN.match(generated)
            if match:
                return add_computed_column(match[1], match[2], match[3].strip())
            return run_select(generated)
        except SpreadsheetError as e:
            # Self-healing: hand the exact failure back and let it try again.
            logger.info("Attempt %d rejected (%s)", attempt + 1, e)
            error = f"-- attempt: {generated}\n-- error: {e}"

    raise SpreadsheetError(
        "Couldn't turn that into a valid query over your spreadsheets. "
        "Try naming the worksheet and columns you mean."
    )
