"""Shared orchestration used by all three ingest endpoints (file/url/text):
dedup check -> LightRAG insert -> manifest row."""

import os
import uuid

import opik
from lightrag.base import DocStatus

from app.services import dedup, manifest
from app.services.lightrag_engine import get_rag

_EXTENSION_SOURCE_TYPE = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
}


class IngestionError(Exception):
    pass


@opik.track(name="ingest_document", ignore_arguments=["text"])
async def ingest_text(text: str, file_name: str, source_type: str) -> dict:
    text = text.strip()
    if not text:
        raise IngestionError("No content to ingest.")

    content_hash = dedup.content_hash(text)
    existing = await manifest.find_by_hash(content_hash)
    if existing:
        return {**existing, "deduped": True}

    doc_id = f"doc-{uuid.uuid4().hex}"
    rag = await get_rag()
    track_id = await rag.ainsert(input=[text], ids=[doc_id], file_paths=[file_name])

    docs = await rag.aget_docs_by_track_id(track_id)
    status_row = docs.get(doc_id)
    if status_row is None:
        # LightRAG refuses any insert whose file basename already has a
        # doc_status row and files the attempt under a synthetic "dup-" id, so
        # our doc_id is simply absent. That is a duplicate, not a failure --
        # and it reached this point instead of the hash check above only
        # because the already-indexed document has no manifest row, which is
        # exactly what _reconcile_manifest repairs.
        await _reconcile_manifest(rag, skip_doc_id=doc_id)
        existing = await manifest.find_by_name(file_name)
        if existing is not None:
            return {**existing, "deduped": True}
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

    return {**record, "deduped": False}


async def _reconcile_manifest(rag, skip_doc_id: str) -> None:
    processed = await rag.doc_status.get_docs_by_status(DocStatus.PROCESSED)
    for doc_id, status_row in processed.items():
        if doc_id == skip_doc_id:
            continue
        if await manifest.get_document(doc_id) is not None:
            continue

        file_name = status_row.file_path or doc_id
        ext = os.path.splitext(file_name)[1].lower()
        await manifest.insert_document(
            doc_id=doc_id,
            file_name=file_name,
            source_type=_EXTENSION_SOURCE_TYPE.get(ext, "unknown"),
            content_hash=f"lightrag-md5:{status_row.content_hash or doc_id}",
            chunk_count=status_row.chunks_count or 0,
            size_bytes=status_row.content_length or 0,
        )


async def delete_document(doc_id: str) -> bool:
    row = await manifest.get_document(doc_id)
    if row is None:
        return False

    rag = await get_rag()
    await rag.adelete_by_doc_id(doc_id)

    # LightRAG's filename dedup matches doc_status rows of ANY status, and each
    # rejected re-upload leaves a "dup-" tombstone behind carrying the same
    # file_path. Deleting only the real document would leave those tombstones
    # blocking the name forever -- delete-then-reupload is the recovery path,
    # so it has to actually free the name.
    stale = await rag.doc_status.get_docs_by_status(DocStatus.FAILED)
    tombstones = [
        stale_id
        for stale_id, status_row in stale.items()
        if stale_id.startswith("dup-") and status_row.file_path == row["file_name"]
    ]
    if tombstones:
        await rag.doc_status.delete(tombstones)

    await manifest.delete_document(doc_id)
    return True
