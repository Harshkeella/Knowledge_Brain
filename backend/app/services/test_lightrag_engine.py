"""Groq daily-quota cooldown: after a tokens-per-day 429, the exhausted model
must be skipped (not retried) until the cooldown expires.
"""

import asyncio

import httpx
from openai import RateLimitError

from app.services import lightrag_engine as le


def _tpd_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError(
        "Rate limit reached ... on tokens per day (TPD): Limit 100000, Used 99999.",
        response=response,
        body=None,
    )


def test_tpd_429_sets_cooldown_and_is_then_skipped(monkeypatch):
    model = "test-model"
    le._groq_model_cooldown_until.pop(model, None)
    monkeypatch.setattr(le._settings, "groq_daily_quota_cooldown_seconds", 60.0)
    monkeypatch.setattr(le._settings, "groq_rate_limit_retries", 2)

    async def fake_complete(*args, **kwargs):
        raise _tpd_error()

    monkeypatch.setattr(le, "openai_complete_if_cache", fake_complete)

    assert not le._groq_model_on_cooldown(model)

    try:
        asyncio.run(
            le._groq_complete_with_rate_limit_retry(
                model, "prompt", None, [], False
            )
        )
        raise AssertionError("expected RateLimitError to propagate")
    except RateLimitError:
        pass

    assert le._groq_model_on_cooldown(model)
