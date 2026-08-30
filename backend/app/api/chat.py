import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from lightrag import QueryParam

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.models.schemas import (
    ChatRequest,
    SessionMessageOut,
    SessionOut,
    SessionRenameRequest,
)
from app.services import (
    chat_store,
    grounding,
    multihop,
    provenance,
    spreadsheet_query,
)
from app.services.lightrag_engine import get_rag

logger = logging.getLogger("app.api.chat")
_settings = get_settings()

# Authenticated, at the router: a route added here cannot forget it, and the
# dependency binds the identity that every store below scopes itself on.
router = APIRouter(
    prefix="/api/v1/chat",
    tags=["chat"],
    dependencies=[Depends(get_current_user)],
)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def build_retrieval_param() -> QueryParam:
    """Retrieval-only params for a multi-hop sub-question: no generation, and
    a smaller budget, since a hop's job is to name entities for the final
    retrieval rather than to assemble a full answer context.

    Built fresh per call -- LightRAG writes resolved keywords back onto the
    QueryParam, so a shared instance would leak one hop's keywords into the
    next.
    """
    return QueryParam(
        mode="mix",
        stream=False,
        enable_rerank=_settings.rerank_enabled,
        chunk_top_k=_settings.rerank_top_n,
        max_total_tokens=_settings.query_context_token_budget // 2,
        max_entity_tokens=_settings.query_context_token_budget // 8,
        max_relation_tokens=_settings.query_context_token_budget // 6,
    )


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


def _describe_table_result(result: dict) -> str:
    if result.get("added_column"):
        return (
            f"Added the **{result['added_column']}** column to `{result['table']}`."
        )
    rows = result["total_row_count"]
    suffix = f" (capped at {rows})" if result["truncated"] else ""
    return f"{rows} row{'' if rows == 1 else 's'}{suffix}."


async def _spreadsheet_answer(message: str, tables: list[str]) -> dict | None:
    """Spreadsheet questions are answered by DuckDB, not by the LLM: the model
    only writes the SQL. Returns None when the question isn't about the
    tabular data, and the normal retrieval path takes over.

    ponytail: a spreadsheet answer doesn't also run document retrieval. Run
    both paths if cross-referencing answers need the prose alongside the rows.
    """
    try:
        return await spreadsheet_query.answer(message, tables)
    except spreadsheet_query.SpreadsheetError as e:
        return {"error": str(e)}


async def _persist(
    session_id: str | None,
    message: str,
    answer: str,
    evidence: list[dict],
) -> None:
    """Write the finished turn to the session store.

    Persistence never breaks a chat: the answer has already streamed to the
    user by the time this runs, so a store failure is logged, not raised.
    """
    if not session_id:
        return
    try:
        session = await chat_store.get_session(session_id)
        if session is None:
            logger.warning("Unknown session %s; turn not persisted", session_id)
            return
        await chat_store.add_message(session_id, "user", message)
        await chat_store.add_message(session_id, "assistant", answer, evidence)
        # First exchange names the thread.
        if session["title"] == chat_store.UNTITLED:
            await chat_store.rename_session(
                session_id, await chat_store.generate_title(message)
            )
    except Exception:
        logger.warning("Persisting chat turn failed", exc_info=True)


async def _chat_stream(
    message: str, history: list[dict], session_id: str | None = None
) -> AsyncIterator[str]:
    rag = await get_rag()

    # Retrieval decides whether this is a data question -- the worksheet and
    # column nodes are indexed like anything else, so a question that names one
    # retrieves it. No LLM call is spent on questions that aren't about data.
    tables = await spreadsheet_query.relevant_tables(rag, message)
    if tables:
        result = await _spreadsheet_answer(message, tables)
        if result is not None and result.get("error"):
            yield _sse({"type": "error", "message": result["error"]})
            yield _sse({"type": "done"})
            return
        if result is not None:
            summary = _describe_table_result(result)
            yield _sse({"type": "table", "result": result})
            yield _sse({"type": "token", "text": summary})
            # A DuckDB answer is exact rows, not retrieved prose -- there is no
            # evidence chain to show and nothing for the grounding check to do.
            await _persist(session_id, message, summary, [])
            yield _sse({"type": "done"})
            return

    param = build_query_param(history)

    # A question that spans two facts gets its early hops retrieved first, and
    # the entities they found are seeded into this call's keywords so the final
    # retrieval searches for them by name. Single-hop questions cost nothing:
    # `gather` returns immediately without an LLM call. See services/multihop.
    seeds = await multihop.gather(rag, message, build_retrieval_param)
    if seeds:
        param.ll_keywords = seeds

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

    data = result.get("data", {})
    references = data.get("references", [])
    sources = [
        {"reference_id": ref.get("reference_id"), "file_path": ref.get("file_path")}
        for ref in references
    ]
    yield _sse({"type": "sources", "sources": sources})

    # What actually reached the LLM, per source. Sent before the answer so the
    # provenance button is live as soon as its source chip renders.
    evidence = provenance.build_evidence(data)
    yield _sse({"type": "evidence", "evidence": evidence})

    llm_response = result.get("llm_response", {})
    answer = ""
    try:
        if llm_response.get("is_streaming"):
            async for chunk in llm_response["response_iterator"]:
                if chunk:
                    answer += chunk
                    yield _sse({"type": "token", "text": chunk})
        else:
            answer = llm_response.get("content") or ""
            if answer:
                yield _sse({"type": "token", "text": answer})
    except Exception as e:
        logger.exception("Chat streaming failed")
        yield _sse({"type": "error", "message": str(e)})
        return

    # Faithfulness pass on the finished text. Flags rather than strips, since
    # the answer has already streamed (see services/grounding.py).
    verdict = grounding.check(answer, evidence)
    if verdict["unsupported"]:
        logger.info(
            "Grounding: %d/%d sentences unsupported",
            len(verdict["unsupported"]),
            verdict["checked"],
        )
        yield _sse({"type": "grounding", "grounding": verdict})

    await _persist(session_id, message, answer, evidence)
    yield _sse({"type": "done"})


@router.post("/stream")
async def chat_stream(payload: ChatRequest):
    history = [h.model_dump() for h in payload.history]
    return StreamingResponse(
        _chat_stream(payload.message, history, payload.session_id),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# --- Sessions -------------------------------------------------------------
# A persistence layer around the query endpoint above, not a replacement for
# it: /stream still answers identically with no session_id.


@router.post("/sessions", response_model=SessionOut)
async def create_session():
    return await chat_store.create_session()


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions():
    return await chat_store.list_sessions()


@router.get("/sessions/{session_id}/messages", response_model=list[SessionMessageOut])
async def list_session_messages(session_id: str):
    if await chat_store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return await chat_store.list_messages(session_id)


@router.patch("/sessions/{session_id}", response_model=SessionOut)
async def rename_session(session_id: str, payload: SessionRenameRequest):
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title cannot be empty")
    if not await chat_store.rename_session(session_id, title[:120]):
        raise HTTPException(status_code=404, detail="Session not found")
    return await chat_store.get_session(session_id)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    if not await chat_store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"id": session_id, "deleted": True}
