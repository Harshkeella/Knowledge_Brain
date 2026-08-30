"""Per-user LLM limits and usage accounting.

The provider budget is shared and finite: on a free Groq tier one user pasting
a book can drain the whole day's tokens before anyone else asks a question.
`rate_limiter.TokenBucket` already paces the *global* extraction budget; this
adds the other half -- a per-user ceiling, so exhaustion is contained to the
user who caused it.

Two ceilings, because they fail differently:

* requests per minute -- a burst, worth *waiting* on (the bucket refills in
  seconds and the caller barely notices);
* tokens per day -- a budget, worth *refusing* (waiting out a daily reset is
  not a request timeout, it is an outage).

Usage is written to the user's own SQLite database, next to their documents,
so "which user consumed the most this month" is a query and not a guess.
"""

import asyncio
import datetime
import logging
import os

import aiosqlite

from app.core import auth
from app.core.config import get_settings
from app.services.rate_limiter import TokenBucket

logger = logging.getLogger("app.llm_limits")
_settings = get_settings()


class LlmQuotaExceeded(Exception):
    """The user is out of LLM budget. Surfaced to them, not retried."""


# One bucket per user, and the per-user concurrency gate. Both are in-process:
# a single API+worker process is the deployment this beta targets, and a
# distributed counter would need Redis to hold state this cheap to rebuild.
#
# ponytail: in-process, so limits are per-process. Move the counters to Redis
# if the API is ever run with more than one replica.
_request_buckets: dict[str, TokenBucket] = {}
_concurrency: dict[str, asyncio.Semaphore] = {}


def _bucket(user_id: str) -> TokenBucket:
    if user_id not in _request_buckets:
        _request_buckets[user_id] = TokenBucket(_settings.llm_user_requests_per_minute)
    return _request_buckets[user_id]


def _gate(user_id: str) -> asyncio.Semaphore:
    if user_id not in _concurrency:
        _concurrency[user_id] = asyncio.Semaphore(
            _settings.llm_user_max_concurrent
        )
    return _concurrency[user_id]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    operation TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    estimated_cost REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at);
"""


def _path() -> str:
    directory = auth.user_dir()
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "usage.sqlite3")


async def _connect():
    db = await aiosqlite.connect(_path())
    await db.executescript(_SCHEMA)
    return db


async def record(
    provider: str,
    model: str,
    operation: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Append one call to this user's ledger.

    Never raises: accounting that can fail a chat turn is worse than
    accounting with a hole in it.
    """
    cost = (
        input_tokens * _settings.llm_cost_per_input_token
        + output_tokens * _settings.llm_cost_per_output_token
    )
    try:
        db = await _connect()
        try:
            await db.execute(
                "INSERT INTO llm_usage (provider, model, operation, input_tokens,"
                " output_tokens, estimated_cost, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    provider,
                    model,
                    operation,
                    int(input_tokens),
                    int(output_tokens),
                    cost,
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                ),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception:
        logger.warning("Recording LLM usage failed", exc_info=True)


async def tokens_used_today() -> int:
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    db = await _connect()
    try:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM llm_usage"
            " WHERE created_at >= ?",
            (day,),
        )
        return int((await cursor.fetchone())[0])
    finally:
        await db.close()


async def summary() -> dict:
    """This user's LLM consumption, for /me/usage."""
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    month = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
    db = await _connect()
    try:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0),"
            "       COALESCE(SUM(estimated_cost), 0), COUNT(*)"
            " FROM llm_usage WHERE created_at >= ?",
            (month,),
        )
        month_tokens, month_cost, calls = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM llm_usage"
            " WHERE created_at >= ?",
            (day,),
        )
        today_tokens = (await cursor.fetchone())[0]
    finally:
        await db.close()

    return {
        "tokens_today": int(today_tokens),
        "tokens_per_day_limit": _settings.llm_user_tokens_per_day,
        "tokens_this_month": int(month_tokens),
        "estimated_cost_this_month": round(float(month_cost), 6),
        "calls_this_month": int(calls),
    }


class user_budget:
    """Async context manager wrapping one LLM call for the current user.

    Enter: wait for the per-minute allowance and a concurrency slot, and refuse
    outright if today's token budget is already spent. Exit: release the slot.
    The token ledger is written by `record` once the real counts are known.
    """

    def __init__(self, estimated_tokens: int):
        self._estimated = estimated_tokens
        self._gate: asyncio.Semaphore | None = None

    async def __aenter__(self):
        user_id = auth.current_user().id

        if _settings.llm_user_tokens_per_day > 0:
            used = await tokens_used_today()
            if used + self._estimated > _settings.llm_user_tokens_per_day:
                raise LlmQuotaExceeded(
                    "You have used today's AI budget "
                    f"({used:,} of {_settings.llm_user_tokens_per_day:,} tokens). "
                    "It resets at midnight UTC."
                )

        # A burst is worth waiting out; the bucket refills in seconds.
        await _bucket(user_id).acquire(1)

        self._gate = _gate(user_id)
        await self._gate.acquire()
        return self

    async def __aexit__(self, *exc):
        if self._gate is not None:
            self._gate.release()
        return False
