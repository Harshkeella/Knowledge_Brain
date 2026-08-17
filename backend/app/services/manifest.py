"""SQLite inventory of what's in the knowledge base — the metadata LightRAG itself
doesn't track (file name, source type, byte size, dedup hash, date added)."""

import datetime
import os

import aiosqlite

from app.core.config import get_settings

_settings = get_settings()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    date_added TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash);
"""


async def init_db() -> None:
    os.makedirs(_settings.storage_dir, exist_ok=True)
    async with aiosqlite.connect(_settings.manifest_path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def find_by_hash(content_hash: str) -> dict | None:
    async with aiosqlite.connect(_settings.manifest_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM documents WHERE content_hash = ? LIMIT 1", (content_hash,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def find_by_name(file_name: str) -> dict | None:
    async with aiosqlite.connect(_settings.manifest_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM documents WHERE file_name = ? LIMIT 1", (file_name,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def insert_document(
    doc_id: str,
    file_name: str,
    source_type: str,
    content_hash: str,
    chunk_count: int,
    size_bytes: int,
) -> dict:
    date_added = datetime.datetime.now(datetime.timezone.utc).isoformat()
    async with aiosqlite.connect(_settings.manifest_path) as db:
        await db.execute(
            """INSERT INTO documents
               (doc_id, file_name, source_type, content_hash, chunk_count, size_bytes, date_added)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                doc_id,
                file_name,
                source_type,
                content_hash,
                chunk_count,
                size_bytes,
                date_added,
            ),
        )
        await db.commit()
    return {
        "doc_id": doc_id,
        "file_name": file_name,
        "source_type": source_type,
        "content_hash": content_hash,
        "chunk_count": chunk_count,
        "size_bytes": size_bytes,
        "date_added": date_added,
    }


async def list_documents() -> list[dict]:
    async with aiosqlite.connect(_settings.manifest_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM documents ORDER BY date_added DESC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_document(doc_id: str) -> dict | None:
    async with aiosqlite.connect(_settings.manifest_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM documents WHERE doc_id = ? LIMIT 1", (doc_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_document(doc_id: str) -> bool:
    async with aiosqlite.connect(_settings.manifest_path) as db:
        cursor = await db.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        await db.commit()
        return cursor.rowcount > 0
