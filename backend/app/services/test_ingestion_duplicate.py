"""Re-uploading an already-indexed file must report "duplicate", not crash.

LightRAG rejects any insert whose file basename already exists in doc_status
and records the attempt under a synthetic "dup-" id, so the doc_id we passed
never comes back. That used to surface as "LightRAG did not report a status
for 'doc-...'" -- on a document that was sitting in the graph, fully indexed.
"""

import asyncio
import types

import pytest

from app.services import ingestion, manifest
from lightrag.base import DocStatus


class _FakeDocStatus:
    def __init__(self, rows):
        self.rows = rows
        self.deleted: list[str] = []

    async def get_docs_by_status(self, status):
        return {k: v for k, v in self.rows.items() if v.status == status.value}

    async def delete(self, ids):
        self.deleted.extend(ids)


def _row(status, file_path, chunks=0):
    return types.SimpleNamespace(
        status=status,
        file_path=file_path,
        chunks_count=chunks,
        content_hash="md5",
        content_length=1234,
        error_msg=None,
    )


@pytest.fixture
def kb(tmp_path, monkeypatch):
    # manifest_path is derived from storage_dir.
    monkeypatch.setattr(manifest._settings, "storage_dir", str(tmp_path))
    asyncio.run(manifest.init_db())


def _fake_rag(rows, *, report_status_for=None):
    """LightRAG's insert reports nothing for our doc_id unless told to."""

    async def ainsert(input, ids, file_paths):
        return "track-1"

    async def aget_docs_by_track_id(track_id):
        return {report_status_for: _row("processed", "x.pdf", 5)} if report_status_for else {}

    return types.SimpleNamespace(
        ainsert=ainsert,
        aget_docs_by_track_id=aget_docs_by_track_id,
        doc_status=_FakeDocStatus(rows),
        adelete_by_doc_id=lambda doc_id: asyncio.sleep(0),
    )


def test_reupload_of_an_indexed_file_returns_deduped_not_an_error(kb, monkeypatch):
    rag = _fake_rag(
        {
            "doc-original": _row(DocStatus.PROCESSED.value, "handbook.pdf", 63),
            "dup-tombstone": _row(DocStatus.FAILED.value, "handbook.pdf"),
        }
    )
    monkeypatch.setattr(ingestion, "get_rag", lambda: _wrap(rag))

    result = asyncio.run(ingestion.ingest_text("some text", "handbook.pdf", "pdf"))

    assert result["deduped"] is True
    assert result["doc_id"] == "doc-original"
    # The inventory was missing the document entirely -- that's why the hash
    # dedup never caught it. Resolving the duplicate has to heal that.
    assert asyncio.run(manifest.find_by_name("handbook.pdf")) is not None


def test_rejected_with_nothing_indexed_reports_a_usable_error(kb, monkeypatch):
    rag = _fake_rag({"dup-tombstone": _row(DocStatus.FAILED.value, "ghost.pdf")})
    monkeypatch.setattr(ingestion, "get_rag", lambda: _wrap(rag))

    with pytest.raises(ingestion.IngestionError, match="Delete it from the knowledge"):
        asyncio.run(ingestion.ingest_text("some text", "ghost.pdf", "pdf"))


def test_delete_frees_the_filename_by_purging_dup_tombstones(kb, monkeypatch):
    rag = _fake_rag({"dup-a": _row(DocStatus.FAILED.value, "handbook.pdf")})
    monkeypatch.setattr(ingestion, "get_rag", lambda: _wrap(rag))
    asyncio.run(
        manifest.insert_document("doc-original", "handbook.pdf", "pdf", "h", 63, 10)
    )

    assert asyncio.run(ingestion.delete_document("doc-original")) is True
    # Left behind, the tombstone blocks that filename from ever being re-added.
    assert rag.doc_status.deleted == ["dup-a"]


async def _wrap(rag):
    return rag
