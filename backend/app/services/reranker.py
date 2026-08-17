"""Second-pass relevance ranking for chat retrieval.

LightRAG's vector search is a bi-encoder: query and chunk are embedded
separately, so it ranks on rough similarity and casts a wide net (top_k=40).
A cross-encoder reads query and chunk *together* and scores the pair
directly -- slower per candidate, so it's useless for search, but it's the
right tool for re-ordering 40 candidates down to the handful that actually
fit in the prompt.

That last part is why this matters more once the context budget is tight:
the budget decides how many chunks survive, the ranking decides which ones.

Local, via the sentence-transformers already used for embeddings -- no new
dependency and no second rate-limited API in the chat path.
"""

import asyncio
import logging
import threading

from app.core.config import get_settings

logger = logging.getLogger("app.reranker")

_settings = get_settings()

_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import CrossEncoder

            logger.info("Loading rerank model %s", _settings.rerank_model)
            _model = CrossEncoder(_settings.rerank_model)
    return _model


def _score(query: str, documents: list[str]) -> list[float]:
    return _get_model().predict([(query, doc) for doc in documents])


async def rerank(
    query: str, documents: list[str], top_n: int | None = None, **kwargs
) -> list[dict]:
    """LightRAG's rerank_model_func contract: returns [{index, relevance_score}]
    over the input `documents`, highest first."""
    if not documents:
        return []

    scores = await asyncio.to_thread(_score, query, documents)
    ranked = sorted(enumerate(scores), key=lambda pair: -pair[1])
    return [
        {"index": index, "relevance_score": float(score)}
        for index, score in ranked[: top_n or len(ranked)]
    ]


def warmup() -> None:
    """Load at boot so the first chat message doesn't pay for it."""
    _get_model()
