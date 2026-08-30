"""Shared orchestration used by all three ingest endpoints (file/url/text):
dedup check -> LightRAG insert -> manifest row -> Source supernode."""

import logging
import os
import uuid

import opik
from lightrag.base import DocStatus

from app.services import dedup, manifest, source_graph
from app.services.lightrag_engine import get_rag

_EXTENSION_SOURCE_TYPE = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
}


class IngestionError(Exception):
    pass


_TEXT_EXTENSIONS = {".md", ".txt", ".markdown"}


async def ingest_file_bytes(data: bytes, file_name: str) -> dict:
    """One uploaded/on-disk file -> a manifest record.

    Lifted verbatim out of the /ingest/file loop so folder ingestion routes its
    document leaves through exactly the same path -- same parsers, same dedup,
    same tabular projection -- rather than growing a second, subtly different
    copy of it.
    """
    import asyncio

    from app.services import source_graph, tabular_graph
    from app.services.parsers.pdf import extract_pdf_text
    from app.services.parsers.spreadsheet import (
        SPREADSHEET_EXTENSIONS,
        build_summary,
        load_spreadsheet,
    )
    from app.services.parsers.text import extract_plain_text

    ext = os.path.splitext(file_name)[1].lower()
    workbook = None
    if ext == ".pdf":
        text = extract_pdf_text(data)
        source_type = "pdf"
    elif ext in SPREADSHEET_EXTENSIONS:
        # Rows go to DuckDB; only the schema summary is indexed, so the
        # graph gains the workbook's structure without its cells.
        workbook = await asyncio.to_thread(load_spreadsheet, data, file_name)
        text = build_summary(workbook)
        source_type = "spreadsheet"
    elif ext in _TEXT_EXTENSIONS:
        text = extract_plain_text(data)
        source_type = "markdown" if ext in (".md", ".markdown") else "text"
    else:
        raise IngestionError(f"Unsupported file type: {ext or '(none)'}")

    record = await ingest_text(text, file_name, source_type)
    if workbook is not None:
        # Deterministic, so it runs on a deduped re-upload too: the workbook
        # was just reloaded into DuckDB and the graph has to match it.
        rag = await get_rag()
        await tabular_graph.project(rag, workbook, record["doc_id"])
        # The projection runs after ingest_text, so the supernode did not see
        # the Workbook node when it looked for its output. Sheets and columns
        # hang off the workbook, so this one edge puts the structure under it.
        await source_graph.attach(
            rag, record, [tabular_graph.workbook_node(file_name)]
        )
    return record


class QuotaExceeded(IngestionError):
    """The user has no room left. A distinct type so the API can answer 413
    rather than the generic 422 every other ingestion failure gets."""


async def _check_quota(incoming_bytes: int) -> None:
    """Refuse an ingest that would put this user over their quota.

    Counted from the extracted text, which is what is actually stored and
    indexed -- and checked BEFORE the LightRAG insert, so an over-quota upload
    costs no embedding, no extraction and no vector writes. A deduped upload
    never reaches here: it consumes nothing new.
    """
    current = await manifest.usage()
    if current["used_bytes"] + incoming_bytes > current["quota_bytes"]:
        raise QuotaExceeded(
            f"This would use {_gb(current['used_bytes'] + incoming_bytes)} of "
            f"your {_gb(current['quota_bytes'])} quota. Delete something first."
        )


def _gb(value: int) -> str:
    return f"{value / 1024**3:.2f} GB"


@opik.track(name="ingest_document", ignore_arguments=["text"])
async def ingest_text(text: str, file_name: str, source_type: str) -> dict:
    text = text.strip()
    if not text:
        raise IngestionError("No content to ingest.")

    content_hash = dedup.content_hash(text)
    await manifest.init_db()
    existing = await manifest.find_by_hash(content_hash)
    if existing:
        return {**existing, "deduped": True}

    await _check_quota(len(text.encode("utf-8")))

    rag = await get_rag()

    # Two attempts: the second one only happens after clearing doc_status rows
    # that were blocking the name (see _clear_blocking_rows).
    for attempt in (0, 1):
        doc_id = f"doc-{uuid.uuid4().hex}"
        track_id = await rag.ainsert(
            input=[text], ids=[doc_id], file_paths=[file_name]
        )
        docs = await rag.aget_docs_by_track_id(track_id)
        status_row = docs.get(doc_id)
        if status_row is not None:
            break

        # LightRAG refuses any insert whose file basename already has a
        # doc_status row and files the attempt under a synthetic "dup-" id, so
        # our doc_id is simply absent. Usually that means the document really
        # is already indexed and only its manifest row is missing, which
        # _reconcile_manifest repairs.
        await _reconcile_manifest(rag, skip_doc_id=doc_id)
        existing = await manifest.find_by_name(file_name)
        if existing is not None:
            return {**existing, "deduped": True}

        cleared = await _clear_blocking_rows(rag, file_name) if attempt == 0 else 0
        if not cleared:
            raise IngestionError(
                f"{file_name!r} was rejected as a duplicate, but no indexed "
                "document with that name exists. Delete it from the knowledge "
                "base and upload again."
            )

    if status_row.status == "failed":
        raise IngestionError(status_row.error_msg or "LightRAG processing failed.")

    chunk_count = status_row.chunks_count or 0
    size_bytes = len(text.encode("utf-8"))

    record = await manifest.insert_document(
        doc_id=doc_id,
        file_name=file_name,
        source_type=source_type,
        content_hash=content_hash,
        chunk_count=chunk_count,
        size_bytes=size_bytes,
    )

    # LightRAG's pipeline opportunistically finishes OTHER pending/failed
    # documents as a side effect of processing this one; make sure any of
    # those that reached PROCESSED also get a manifest row, or they end up
    # fully indexed but invisible in the knowledge-base inventory.
    await _reconcile_manifest(rag, skip_doc_id=doc_id)

    await _register_source(rag, record)

    return {**record, "deduped": False}


async def _register_source(rag, record: dict) -> None:
    """Attach the ingestion's supernode. The document is already indexed by the
    time this runs, so a failure here is a missing handle on real content, not
    a failed upload -- log it rather than reporting an ingest that worked as
    one that did not.
    """
    try:
        await source_graph.register(rag, record)
    except Exception:
        logging.getLogger("app.ingestion").warning(
            "Indexed %s but failed to write its Source node",
            record["file_name"],
            exc_info=True,
        )


async def _clear_blocking_rows(rag, file_name: str) -> int:
    """Free a filename that a document which never finished is holding.

    LightRAG's dedup matches the file name against doc_status rows of ANY
    status, so a document that failed mid-pipeline blocks its own name for
    good -- and it has no manifest row, so there is nothing in the inventory to
    delete. That dead end is what "rejected as a duplicate, but no indexed
    document with that name exists" was. Only rows that never reached
    PROCESSED are cleared; a real indexed document is never touched.
    """
    unfinished = (
        DocStatus.PENDING,
        DocStatus.PARSING,
        DocStatus.ANALYZING,
        DocStatus.PROCESSING,
        DocStatus.PREPROCESSED,
        DocStatus.FAILED,
    )
    blocking: list[str] = []
    for status in unfinished:
        rows = await rag.doc_status.get_docs_by_status(status)
        blocking += [
            doc_id for doc_id, row in rows.items() if row.file_path == file_name
        ]
    if blocking:
        await rag.doc_status.delete(blocking)
    return len(blocking)


async def _reconcile_manifest(rag, skip_doc_id: str) -> None:
    processed = await rag.doc_status.get_docs_by_status(DocStatus.PROCESSED)
    for doc_id, status_row in processed.items():
        if doc_id == skip_doc_id:
            continue
        if await manifest.get_document(doc_id) is not None:
            continue

        file_name = status_row.file_path or doc_id
        ext = os.path.splitext(file_name)[1].lower()
        record = await manifest.insert_document(
            doc_id=doc_id,
            file_name=file_name,
            source_type=_EXTENSION_SOURCE_TYPE.get(ext, "unknown"),
            content_hash=f"lightrag-md5:{status_row.content_hash or doc_id}",
            chunk_count=status_row.chunks_count or 0,
            size_bytes=status_row.content_length or 0,
        )
        # Recovered documents are real documents; they get a supernode too.
        await _register_source(rag, record)


async def delete_document(doc_id: str) -> bool:
    row = await manifest.get_document(doc_id)
    if row is None:
        return False

    rag = await get_rag()

    if row["source_type"] == "folder":
        # A folder has no LightRAG document behind it -- only graph nodes -- so
        # it skips adelete_by_doc_id and the doc_status cleanup entirely.
        # Documents indexed from inside it keep their own inventory rows: they
        # were ingested as documents and are deleted as documents.
        from app.services import folder_ingest

        # remove() takes the supernode with the tree -- deliberately one call.
        await folder_ingest.remove(rag, row["file_name"])
        await manifest.delete_document(doc_id)
        return True

    if row["source_type"] == "spreadsheet":
        from app.services import tabular_graph
        from app.services.parsers.spreadsheet import drop_workbook_tables

        # Order matters: the workbook's DuckDB metadata is what names the graph
        # nodes to remove, so it has to outlive them.
        await tabular_graph.remove(rag, row["file_name"])
        drop_workbook_tables(row["file_name"])

    await source_graph.remove(rag, row["file_name"])

    await rag.adelete_by_doc_id(doc_id)

    # Every rejected re-upload leaves a "dup-" tombstone carrying the same
    # file_path, and a run that failed leaves its own row. Deleting only the
    # real document would leave those blocking the name forever --
    # delete-then-reupload is the recovery path, so it has to free the name.
    await _clear_blocking_rows(rag, row["file_name"])

    await manifest.delete_document(doc_id)
    return True
