"""Regression tests for the three bugs a 200-page-PDF ingest surfaced:

A. the query role's context budget exceeded its model's per-minute ceiling, so
   every chat call 429'd and silently answered from the OpenRouter fallback,
B. nothing capped the assembled context, so ~29,000 tokens went into a prompt
   that had ~6,000 to spend,
C. graph truncation left nodes with no surviving edges floating in the layout.
"""

import asyncio
import types

import pytest

from app.api import chat, graph
from app.core.config import get_settings
from app.services import lightrag_engine as le
from app.services import reranker

_settings = get_settings()

# What a chat call costs beyond the retrieved context: the answer itself.
_ANSWER_HEADROOM = le._ANSWER_HEADROOM_TOKENS


# --- Bug A ------------------------------------------------------------------


def test_query_model_tpm_ceiling_fits_a_whole_chat_call():
    """8b-instant (6,000 TPM) could not fit one query. Fail loudly if someone
    points GROQ_MODEL back at a model that small."""
    assert (
        _settings.query_context_token_budget + _ANSWER_HEADROOM
        <= _settings.groq_tpm_limit
    )


def test_boot_warns_when_the_budget_cannot_fit_the_tpm_ceiling(monkeypatch, caplog):
    monkeypatch.setattr(_settings, "groq_api_key", "test-key")
    monkeypatch.setattr(_settings, "groq_tpm_limit", 6000)
    monkeypatch.setattr(_settings, "query_context_token_budget", 30_000)

    with caplog.at_level("WARNING"):
        le._warn_if_context_budget_exceeds_tpm()

    assert "will 429" in caplog.text


# --- Bug B ------------------------------------------------------------------


def test_assembled_context_is_capped_below_the_budget():
    param = chat.build_query_param([])

    assert param.max_total_tokens == _settings.query_context_token_budget
    # The KG half is budgeted separately from chunks; if these two alone
    # exceed the total, the budget is decorative and chunks get starved.
    assert (
        param.max_entity_tokens + param.max_relation_tokens
        < _settings.query_context_token_budget
    )
    assert param.chunk_top_k == _settings.rerank_top_n


def test_rerank_is_on_and_orders_by_cross_encoder_score(monkeypatch):
    assert chat.build_query_param([]).enable_rerank is True

    monkeypatch.setattr(reranker, "_score", lambda q, docs: [0.1, 0.9, 0.5])
    ranked = asyncio.run(reranker.rerank("q", ["low", "high", "mid"], top_n=2))

    assert [r["index"] for r in ranked] == [1, 2]
    assert ranked[0]["relevance_score"] == pytest.approx(0.9)


# --- Bug C ------------------------------------------------------------------


def _fake_kg():
    """Truncation keeps top-degree nodes; "lonely" is one whose only neighbour
    fell outside the cut, so it arrives with no edges."""
    node = lambda name: types.SimpleNamespace(id=name, properties={})  # noqa: E731
    edge = types.SimpleNamespace(
        id="a-b", source="a", target="b", properties={"weight": 1.0}
    )
    return types.SimpleNamespace(
        nodes=[node("a"), node("b"), node("lonely")],
        edges=[edge],
        is_truncated=True,
    )


def test_truncated_graph_has_no_isolated_nodes_or_orphaned_edges(monkeypatch):
    async def fake_get_rag():
        return types.SimpleNamespace(
            get_knowledge_graph=lambda *a, **kw: asyncio.sleep(0, _fake_kg())
        )

    monkeypatch.setattr(graph, "get_rag", fake_get_rag)

    result = asyncio.run(graph.get_graph(label="*", max_depth=3, max_nodes=2))
    ids = {n.id for n in result.nodes}

    assert "lonely" not in ids
    assert ids == {"a", "b"}
    for e in result.edges:
        assert e.source in ids and e.target in ids
