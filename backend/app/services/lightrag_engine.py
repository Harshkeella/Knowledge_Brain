"""Owns the single LightRAG instance: local storage, local embeddings, Groq/Ollama LLM."""

import asyncio
import logging
import re
import time
from typing import AsyncIterator

import httpx
import opik
from openai import RateLimitError

from lightrag import LightRAG, RoleLLMConfig
from lightrag.llm.openai import openai_complete_if_cache
from lightrag.utils import EmbeddingFunc
from sentence_transformers import SentenceTransformer

from app.core.config import get_settings
from app.services.rate_limiter import TokenBucket, estimate_tokens

logger = logging.getLogger("app.lightrag_engine")

_settings = get_settings()

# Extraction only: many parallel calls over chunks is what exhausts the TPM
# bucket. Chat is one call at a time and stays unpaced so it isn't slowed down.
_extract_budget = TokenBucket(_settings.groq_tpm_limit)
_st_model: SentenceTransformer | None = None


def _get_sentence_transformer() -> SentenceTransformer:
    global _st_model
    if _st_model is None:
        logger.info("Loading local embedding model %s", _settings.embedding_model)
        _st_model = SentenceTransformer(_settings.embedding_model)
    return _st_model


async def _embed(texts: list[str]):
    model = _get_sentence_transformer()
    return await asyncio.to_thread(model.encode, texts, normalize_embeddings=True)


# Set by check_ollama_fallback() at boot. Calling Ollama with a model that was
# never pulled 404s per chunk and fails the whole document, so a known-bad
# fallback is worse than no fallback: it hides the real cause (Groq quota) under
# a NotFoundError traceback.
_ollama_ready = False


@opik.track(name="ollama_complete", type="llm")
async def _ollama_complete(
    prompt, system_prompt, history_messages, keyword_extraction, **kwargs
) -> str | AsyncIterator[str]:
    if not _ollama_ready:
        raise RuntimeError(
            f"No LLM available: Groq failed and the Ollama fallback model "
            f"{_settings.ollama_model!r} is not pulled. Run "
            f"`ollama pull {_settings.ollama_model}` or set OPENROUTER_API_KEY."
        )
    return await openai_complete_if_cache(
        _settings.ollama_model,
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        keyword_extraction=keyword_extraction,
        api_key="ollama",
        base_url=_settings.ollama_base_url,
        **kwargs,
    )


# Groq's 429 body carries both the model's real TPM ceiling and how long until
# it clears: "... (TPM): Limit 6000, Used 3286, Requested 2757. Please try again
# in 430ms." Trusting those beats a hand-maintained config value and a flat
# guessed sleep — the limit differs per model and changes with your tier.
_TPM_LIMIT_RE = re.compile(r"\(TPM\): Limit (\d+)")
_RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)(ms|s)")


def _learn_tpm_limit(exc: RateLimitError) -> None:
    match = _TPM_LIMIT_RE.search(str(exc))
    if match and int(match.group(1)) != _extract_budget.capacity:
        limit = int(match.group(1))
        logger.warning(
            "Groq reports a %d TPM ceiling; resizing the extraction budget "
            "(was %.0f). Set GROQ_TPM_LIMIT=%d to skip this on the next boot.",
            limit,
            _extract_budget.capacity,
            limit,
        )
        _extract_budget.resize(limit)


def _retry_after_seconds(exc: RateLimitError, default: float) -> float:
    match = _RETRY_AFTER_RE.search(str(exc))
    if not match:
        return default
    seconds = float(match.group(1))
    return seconds / 1000 if match.group(2) == "ms" else seconds


def _is_long_window_rate_limit(exc: RateLimitError) -> bool:
    """Distinguish a per-minute throttle (worth waiting out) from a per-day
    quota exhaustion (won't reset for tens of minutes — no point retrying)."""
    text = str(exc).lower()
    return "tokens per day" in text or "(tpd)" in text


# Per-model daily-quota cooldown: once a model reports a TPD rate limit, every
# other in-flight chunk was about to make the same doomed Groq call (each
# paying openai_complete_if_cache's own 3x retry backoff first). Remembering
# the exhausted model for a while skips straight to Ollama instead of
# rediscovering the same 429 on every remaining chunk.
_groq_model_cooldown_until: dict[str, float] = {}


def _groq_model_on_cooldown(model: str) -> bool:
    return time.monotonic() < _groq_model_cooldown_until.get(model, 0.0)


@opik.track(name="groq_complete", type="llm")
async def _groq_complete_with_rate_limit_retry(
    model: str, prompt, system_prompt, history_messages, keyword_extraction, **kwargs
) -> str | AsyncIterator[str]:
    """Call Groq, riding out 429s by waiting for its ~60s rate-limit window to
    reset instead of giving up. lightrag's own openai_complete_if_cache already
    retries RateLimitError 3x internally (4-10s backoff); this adds a few more
    rounds with a longer wait, since that's still far faster than falling back
    to a CPU-bound Ollama call for the rest of a large document. A daily-quota
    exhaustion is a different failure mode — that won't clear for tens of
    minutes, so it skips straight to fallback instead of wasting retries.
    """
    attempts = _settings.groq_rate_limit_retries + 1
    for attempt in range(attempts):
        try:
            return await openai_complete_if_cache(
                model,
                prompt,
                system_prompt=system_prompt,
                history_messages=history_messages,
                keyword_extraction=keyword_extraction,
                api_key=_settings.groq_api_key,
                base_url="https://api.groq.com/openai/v1",
                **kwargs,
            )
        except RateLimitError as e:
            if _is_long_window_rate_limit(e):
                _groq_model_cooldown_until[model] = (
                    time.monotonic() + _settings.groq_daily_quota_cooldown_seconds
                )
                raise
            _learn_tpm_limit(e)
            if attempt == attempts - 1:
                raise
            wait = _retry_after_seconds(e, _settings.groq_rate_limit_wait_seconds)
            logger.warning(
                "Groq rate-limited on %s (attempt %d/%d); waiting %.1fs",
                model,
                attempt + 1,
                attempts,
                wait,
            )
            await asyncio.sleep(wait)


@opik.track(name="openrouter_complete", type="llm")
async def _openrouter_complete(
    prompt, system_prompt, history_messages, keyword_extraction, **kwargs
) -> str | AsyncIterator[str]:
    return await openai_complete_if_cache(
        _settings.openrouter_model,
        prompt,
        system_prompt=system_prompt,
        history_messages=history_messages,
        keyword_extraction=keyword_extraction,
        api_key=_settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
        **kwargs,
    )


@opik.track(name="lightrag_extract_llm", type="llm")
async def llm_model_func(
    prompt,
    system_prompt=None,
    history_messages=None,
    keyword_extraction=False,
    **kwargs,
) -> str | AsyncIterator[str]:
    """Graph building (entity/relationship + keyword extraction): Groq's small
    extraction model, paced by the TPM bucket, falling back to Ollama. Used for
    the default role and the "extract"/"keyword" roles.
    """
    history_messages = history_messages or []

    if _settings.groq_api_key and not _groq_model_on_cooldown(
        _settings.groq_extract_model
    ):
        await _extract_budget.acquire(estimate_tokens(prompt, system_prompt))
        try:
            return await _groq_complete_with_rate_limit_retry(
                _settings.groq_extract_model,
                prompt,
                system_prompt,
                history_messages,
                keyword_extraction,
                **kwargs,
            )
        except Exception as e:
            logger.warning("Groq extraction failed (%s); falling back", e)

    # Same chain as the query role: Groq -> OpenRouter -> Ollama. Extraction
    # burns the daily Groq quota long before chat does, so it needs the
    # cross-provider fallback more, not less.
    if _settings.openrouter_api_key:
        try:
            return await _openrouter_complete(
                prompt, system_prompt, history_messages, keyword_extraction, **kwargs
            )
        except Exception as e:
            logger.warning("OpenRouter call failed (%s); falling back to Ollama", e)

    return await _ollama_complete(
        prompt, system_prompt, history_messages, keyword_extraction, **kwargs
    )


@opik.track(name="lightrag_query_llm", type="llm")
async def query_llm_func(
    prompt,
    system_prompt=None,
    history_messages=None,
    keyword_extraction=False,
    **kwargs,
) -> str | AsyncIterator[str]:
    """Answering (chat query generation): Groq's large model (GROQ_MODEL), kept
    on a different model from extraction so chat and ingest don't share a
    per-model rate-limit bucket, falling back to OpenRouter, then Ollama.
    OpenRouter's free-tier auto-router queues badly under load, so it's a
    fallback rather than primary — Groq is the low-latency path.
    """
    history_messages = history_messages or []

    if _settings.groq_api_key and not _groq_model_on_cooldown(_settings.groq_model):
        try:
            return await _groq_complete_with_rate_limit_retry(
                _settings.groq_model,
                prompt,
                system_prompt,
                history_messages,
                keyword_extraction,
                **kwargs,
            )
        except Exception as e:
            logger.warning(
                "Groq query call failed (%s); falling back to OpenRouter", e
            )

    if _settings.openrouter_api_key:
        try:
            return await _openrouter_complete(
                prompt, system_prompt, history_messages, keyword_extraction, **kwargs
            )
        except Exception as e:
            logger.warning("OpenRouter call failed (%s); falling back to Ollama", e)

    return await _ollama_complete(
        prompt, system_prompt, history_messages, keyword_extraction, **kwargs
    )


async def check_ollama_fallback() -> bool:
    """Ollama is the last-resort fallback for both roles, so a model that was
    never pulled turns a recoverable Groq failure into a failed document — as a
    404 mid-extraction. Surface it at boot instead."""
    global _ollama_ready
    _ollama_ready = False
    base = _settings.ollama_base_url.removesuffix("/v1").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/api/tags")
            resp.raise_for_status()
            pulled = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception as e:
        logger.warning(
            "Ollama unreachable at %s (%s) — Groq failures will have no fallback",
            base,
            e,
        )
        return False

    # Ollama reports "llama3.2:latest"; OLLAMA_MODEL is usually untagged.
    if not any(
        name == _settings.ollama_model or name.split(":")[0] == _settings.ollama_model
        for name in pulled
    ):
        logger.warning(
            "OLLAMA_MODEL %r is not pulled (available: %s) — Groq failures will "
            "fail extraction instead of falling back. Fix with: ollama pull %s",
            _settings.ollama_model,
            ", ".join(pulled) or "none",
            _settings.ollama_model,
        )
        return False

    logger.info("Ollama fallback ready: %s", _settings.ollama_model)
    _ollama_ready = True
    return True


# A chat call sends the whole assembled context in one request, so a context
# budget above the query model's per-minute ceiling can never succeed -- no
# amount of retrying or backoff fits a 30,000-token request through a 6,000
# TPM window. Say so at boot instead of on every request.
_ANSWER_HEADROOM_TOKENS = 2000


def _warn_if_context_budget_exceeds_tpm() -> None:
    if not _settings.groq_api_key:
        return
    needed = _settings.query_context_token_budget + _ANSWER_HEADROOM_TOKENS
    if needed > _settings.groq_tpm_limit:
        logger.warning(
            "Query role %s has a %d TPM ceiling but a chat call needs ~%d tokens "
            "(QUERY_CONTEXT_TOKEN_BUDGET=%d + answer headroom). Every chat query "
            "will 429 and fall back to OpenRouter/Ollama. Lower "
            "QUERY_CONTEXT_TOKEN_BUDGET or move GROQ_MODEL to a higher-TPM model.",
            _settings.groq_model,
            _settings.groq_tpm_limit,
            needed,
            _settings.query_context_token_budget,
        )


_rag: LightRAG | None = None


async def get_rag() -> LightRAG:
    global _rag
    if _rag is None:
        await check_ollama_fallback()
        _warn_if_context_budget_exceeds_tpm()
        # binding/model are what LightRAG's boot-time "Role LLM Configuration"
        # log prints; without them every role reports None/None and the log
        # can't answer "which model is actually serving chat?".
        role_llm_configs = {
            "query": RoleLLMConfig(
                func=query_llm_func,
                metadata={
                    "is_cross_provider": True,
                    "binding": "groq",
                    "model": _settings.groq_model,
                },
            ),
            "keyword": RoleLLMConfig(
                metadata={"binding": "groq", "model": _settings.groq_extract_model},
            ),
            "vlm": RoleLLMConfig(
                metadata={"binding": "groq", "model": _settings.groq_extract_model},
            ),
        }
        # Only the "extract" role moves to the local encoder: "keyword" (query
        # keyword extraction) and "query" still need a generative model.
        if _settings.extraction_backend == "gliner":
            from app.services.gliner_extract import gliner_extract

            role_llm_configs["extract"] = RoleLLMConfig(
                func=gliner_extract,
                max_async=_settings.gliner_max_async,
                metadata={
                    "is_cross_provider": True,
                    "binding": "gliner",
                    "model": _settings.gliner_model,
                },
            )
        else:
            role_llm_configs["extract"] = RoleLLMConfig(
                metadata={"binding": "groq", "model": _settings.groq_extract_model},
            )

        rerank_model_func = None
        if _settings.rerank_enabled:
            from app.services.reranker import rerank as rerank_model_func

        _rag = LightRAG(
            working_dir=_settings.kb_working_dir,
            llm_model_func=llm_model_func,
            role_llm_configs=role_llm_configs,
            rerank_model_func=rerank_model_func,
            embedding_func=EmbeddingFunc(
                embedding_dim=_settings.embedding_dim,
                max_token_size=_settings.chunk_token_size,
                func=_embed,
            ),
            chunk_token_size=_settings.chunk_token_size,
            chunk_overlap_token_size=_settings.chunk_overlap_token_size,
            entity_extract_max_gleaning=_settings.entity_extract_max_gleaning,
            llm_model_max_async=_settings.llm_model_max_async,
            # JSON-structured extraction output is far more robust than the
            # default delimiter format on weaker models (e.g. the local
            # Ollama fallback) — a malformed delimiter mid-response drops
            # the rest of that chunk's entities/relations, whereas
            # json_repair can recover a slightly-off JSON response.
            entity_extraction_use_json=True,
        )
        await _rag.initialize_storages()
    return _rag


async def shutdown_rag() -> None:
    global _rag
    if _rag is not None:
        await _rag.finalize_storages()
        _rag = None
