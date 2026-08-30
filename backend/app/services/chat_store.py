"""SQLite store for chat sessions and messages.

App state, deliberately kept out of the knowledge graph and the vector store:
a session list is a mutable, per-user, chronological thing, and neither
NetworkX-on-disk nor Qdrant is the right shape for it. Same aiosqlite pattern
and same storage dir as `manifest.py`, a separate file so a knowledge-base
rebuild (which deletes storage/kb) never takes the chat history with it.

The evidence chain persisted on an assistant message is the JSON returned by
the retrieval pipeline, stored verbatim -- that is what makes a provenance
panel still work when an old session is reopened.
"""

import datetime
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

import aiosqlite

from app.core import auth
from app.core.config import get_settings

logger = logging.getLogger("app.services.chat_store")
_settings = get_settings()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    evidence TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated
    ON chat_sessions(updated_at DESC);
"""

UNTITLED = "New chat"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _path() -> str:
    """This user's chat database. One file per user (see manifest._path)."""
    directory = auth.user_dir()
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "chat.sqlite3")


@asynccontextmanager
async def _connect():
    """A connection with the row factory and the cascade pragma already set.

    Must stay a context manager rather than returning the connection: an
    aiosqlite `Connection` is a Thread that starts when it is awaited, and
    `async with await connect()` would start that thread twice.
    """
    async with aiosqlite.connect(_path()) as db:
        # Off by default in SQLite, and the messages->session cascade needs it.
        await db.execute("PRAGMA foreign_keys = ON")
        db.row_factory = aiosqlite.Row
        yield db


async def init_db() -> None:
    async with aiosqlite.connect(_path()) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def create_session(title: str = UNTITLED) -> dict:
    session = {
        "id": str(uuid.uuid4()),
        "title": title,
        "created_at": _now(),
        "updated_at": _now(),
    }
    async with _connect() as db:
        await db.execute(
            "INSERT INTO chat_sessions (id, title, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (
                session["id"],
                session["title"],
                session["created_at"],
                session["updated_at"],
            ),
        )
        await db.commit()
    return session


async def list_sessions() -> list[dict]:
    """Most recently used first. The sidebar does the Today/Yesterday grouping
    from `updated_at` -- grouping here would bake the viewer's timezone into
    the API."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM chat_sessions ORDER BY updated_at DESC"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_session(session_id: str) -> dict | None:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def rename_session(session_id: str, title: str) -> bool:
    async with _connect() as db:
        cursor = await db.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), session_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_session(session_id: str) -> bool:
    async with _connect() as db:
        cursor = await db.execute(
            "DELETE FROM chat_sessions WHERE id = ?", (session_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def add_message(
    session_id: str,
    role: str,
    content: str,
    evidence: list | None = None,
) -> dict:
    """Appending a message bumps the session's `updated_at`, which is what
    orders the sidebar -- so an old thread you replied to today sorts to the
    top rather than staying buried at its creation date."""
    message = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "role": role,
        "content": content,
        "evidence": evidence or [],
        "created_at": _now(),
    }
    async with _connect() as db:
        await db.execute(
            "INSERT INTO chat_messages"
            " (id, session_id, role, content, evidence, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                message["id"],
                session_id,
                role,
                content,
                json.dumps(message["evidence"]) if evidence else None,
                message["created_at"],
            ),
        )
        await db.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (message["created_at"], session_id),
        )
        await db.commit()
    return message


def _decode_evidence(raw: str | None) -> list:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        # A malformed blob must not take the whole conversation down with it;
        # the message still reads fine without its provenance panel.
        logger.warning("Discarding unreadable evidence chain")
        return []


async def list_messages(session_id: str) -> list[dict]:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        rows = await cursor.fetchall()
    return [
        {**dict(row), "evidence": _decode_evidence(row["evidence"])} for row in rows
    ]


def fallback_title(message: str) -> str:
    """Never leave a session untitled: the first line of the question, clipped."""
    first = (message or "").strip().splitlines()[0] if (message or "").strip() else ""
    if not first:
        return UNTITLED
    return first[:47].rstrip() + "..." if len(first) > 50 else first


async def generate_title(message: str) -> str:
    """A few words naming the thread. Runs on the cheap extraction model, and
    degrades to the truncated question on any failure -- a title is never worth
    failing a chat turn over."""
    from app.services.lightrag_engine import llm_model_func

    try:
        raw = await llm_model_func(
            f"Question:\n{message}\n\nTitle:",
            system_prompt=(
                "You name chat threads. Reply with a title of at most six words "
                "for the question, in plain text. No quotes, no punctuation at "
                "the end, no preamble, no explanation."
            ),
        )
        if not isinstance(raw, str):
            return fallback_title(message)
        title = raw.strip().strip('"').strip().splitlines()[0].strip()
        # A model that ignores the instruction and writes a paragraph gets
        # treated as a failure rather than pasted into the sidebar.
        if title and len(title) <= 60:
            return title
    except Exception:
        logger.warning("Title generation failed; using the question", exc_info=True)
    return fallback_title(message)
