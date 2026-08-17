import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from lightrag import QueryParam

from app.core.config import get_settings
from app.models.schemas import ChatRequest
from app.services.lightrag_engine import get_rag

logger = logging.getLogger("app.api.chat")
_settings = get_settings()

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def build_query_param(history: list[dict]) -> QueryParam:
    return QueryParam(
        mode="mix",
        stream=True,
        conversation_history=history,
        include_references=True,
        enable_rerank=_settings.rerank_enabled,
        # Keep only the top reranked chunks, and cap the whole assembled
        # context (entities + relations + chunks + system prompt). LightRAG's
        # 30,000-token default is larger than any free-tier model's per-minute
        # ceiling, so every chat call 429'd on the primary model.
        chunk_top_k=_settings.rerank_top_n,
        max_total_tokens=_settings.query_context_token_budget,
        # max_total_tokens only budgets the CHUNK half -- the KG half is capped
        # separately (LightRAG defaults: 6,000 entity + 8,000 relation tokens),
        # so leaving these at their defaults lets the context blow past the
        # budget on entities alone and starves chunks to zero. Derived from the
        # one knob rather than three so they can't drift apart.
        max_entity_tokens=_settings.query_context_token_budget // 4,
        max_relation_tokens=_settings.query_context_token_budget // 3,
        response_type=(
            "the shortest possible answer that fully answers the question — "
            "a single short paragraph or a tight bullet list, no filler sections"
        ),
        user_prompt=(
            "Be precise and to the point. Do not add an introduction, analysis, or "
            "conclusion section for a simple factual question — just answer it. "
            "Use ONLY the retrieved knowledge base context; never fall back on "
            "outside/general knowledge, even for a follow-up or comparison question "
            "that continues the conversation history — re-ground every answer in the "
            "current retrieved context, not in what you already know. If the context "
            "does not contain the answer, reply with exactly one short sentence "
            "saying the knowledge base has no information on this, and stop there — "
            "do not speculate or pad the response."
        ),
    )


async def _chat_stream(message: str, history: list[dict]) -> AsyncIterator[str]:
    rag = await get_rag()
    param = build_query_param(history)

    try:
        result = await rag.aquery_llm(message, param=param)
    except Exception as e:
        logger.exception("Chat query failed")
        yield _sse({"type": "error", "message": str(e)})
        return

    if result.get("status") == "failure":
        yield _sse(
            {"type": "error", "message": result.get("message", "Query failed.")}
        )
        return

    references = result.get("data", {}).get("references", [])
    sources = [
        {"reference_id": ref.get("reference_id"), "file_path": ref.get("file_path")}
        for ref in references
    ]
    yield _sse({"type": "sources", "sources": sources})

    llm_response = result.get("llm_response", {})
    try:
        if llm_response.get("is_streaming"):
            async for chunk in llm_response["response_iterator"]:
                if chunk:
                    yield _sse({"type": "token", "text": chunk})
        else:
            content = llm_response.get("content") or ""
            if content:
                yield _sse({"type": "token", "text": content})
    except Exception as e:
        logger.exception("Chat streaming failed")
        yield _sse({"type": "error", "message": str(e)})
        return

    yield _sse({"type": "done"})


@router.post("/stream")
async def chat_stream(payload: ChatRequest):
    history = [h.model_dump() for h in payload.history]
    return StreamingResponse(
        _chat_stream(payload.message, history),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
