"""The supernode: one graph node per ingestion event.

Every upload already leaves a row in the SQLite manifest (name, type, size,
chunk count, hash, timestamp). What it did not leave was anything you could
*click*: the entities a PDF produced carry its name in `file_path`, but there
was no node joining them, so "show me everything that came out of this upload"
was a property filter rather than one hop.

This adds that node -- label `source`, one `HAS_ROOT` edge to each node the
ingestion put in the graph. The manifest stays the system of record for
metadata; this is the graph's handle on it, and the Graph Explorer renders it
with no frontend change because the label rides on `entity_type` like every
other label.

Status lifecycle, for folder ingestion only: `processing -> completed|failed`
on the node, patched by `set_status`. The other six ingestors run synchronously
inside one request and raise before the manifest row (and therefore this node)
exists, so for them `processing` is a state nothing could ever observe. A
folder's document pass is detached and takes minutes, so for that one it is.
"""

import logging

from lightrag.constants import GRAPH_FIELD_SEP

from app.services import graph_schema as gs

logger = logging.getLogger("app.source_graph")

_TYPE_DESCRIPTION = {
    "pdf": "PDF document",
    "markdown": "Markdown document",
    "text": "Text document",
    "spreadsheet": "Spreadsheet workbook",
    "youtube": "YouTube transcript",
    "article": "Web article",
    "article_clipper": "Web page captured by the browser extension",
    "paste": "Pasted text",
    "folder": "Folder",
}


def source_node(file_name: str) -> str:
    """Prefixed so it can never collide with a Workbook node, which is named
    by the bare file name."""
    return f"source:{file_name}"


def _describe(record: dict) -> str:
    kind = _TYPE_DESCRIPTION.get(record["source_type"], record["source_type"])
    return (
        f"{kind} {record['file_name']!r}, ingested {record['date_added']}: "
        f"{record['chunk_count']} chunk(s), {record['size_bytes']} bytes. "
        f"The source everything below it was extracted from."
    )


async def attach(rag, record: dict, node_ids: list[str]) -> None:
    """Point the Source node at nodes this ingestion produced. Idempotent."""
    node = source_node(record["file_name"])
    for target in node_ids:
        if target == node:
            continue
        await gs.upsert_edge(
            rag,
            node,
            target,
            gs.HAS_ROOT,
            description=f"{target} came from {record['file_name']}.",
            file_path=record["file_name"],
            source_id=record["doc_id"],
        )


async def create(rag, record: dict, **counts) -> str:
    """Write just the Source node. Returns its node id.

    Split out from `register` for ingestors that already know exactly what they
    produced -- a folder walk has the list in hand and must not pay for a scan
    to rediscover it.
    """
    node = source_node(record["file_name"])
    await gs.upsert_node(
        rag,
        node,
        gs.SOURCE,
        description=_describe(record),
        file_path=record["file_name"],
        source_id=record["doc_id"],
        source_type=record["source_type"],
        ingested_at=record["date_added"],
        size_bytes=record["size_bytes"],
        chunk_count=record["chunk_count"],
        **counts,
    )
    return node


async def set_status(rag, file_name: str, **properties) -> None:
    """Patch the Source node's lifecycle fields.

    The docstring above says there is no status lifecycle, and for six of the
    seven ingestors that is still true -- they finish inside one request, so
    `processing` is a state nothing could observe. Folder ingestion is the
    exception: its document pass runs detached, so `processing` is exactly
    what you would see if you looked while it ran.
    """
    await gs.update_node(rag, source_node(file_name), **properties)


async def register(rag, record: dict) -> None:
    """Create the Source node for a finished ingestion and link everything the
    ingestion wrote to the graph under it.

    ponytail: finds its own output by scanning every node's `file_path`, which
    is O(graph) per upload. That is a dict walk over an in-memory NetworkX
    graph -- fine into the tens of thousands of nodes. If the graph outgrows
    that, have the extractor report the names it wrote instead of rediscovering
    them here.
    """
    node = await create(rag, record)

    file_name = record["file_name"]
    produced = [
        n["id"]
        for n in await rag.chunk_entity_relation_graph.get_all_nodes()
        # Exact match on a separator-joined list -- substring matching would
        # make "report.pdf" claim the nodes of "old report.pdf".
        if file_name in str(n.get("file_path") or "").split(GRAPH_FIELD_SEP)
    ]
    await attach(rag, record, produced)
    await gs.flush(rag)
    logger.info("Source %s linked to %d node(s)", file_name, len(produced))


async def remove(rag, file_name: str) -> None:
    """Drop a Source node. Its HAS_ROOT edges go with it; the nodes it pointed
    at are the document's own and are deleted by LightRAG, not here."""
    await gs.remove_nodes(rag, [source_node(file_name)])
    await gs.flush(rag)
