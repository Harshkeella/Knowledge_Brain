import asyncio

import pytest

from app.services import multihop


@pytest.mark.parametrize(
    "question",
    [
        "Compare the Q3 and Q4 revenue figures.",
        "What is the difference between the two policies?",
        "Show me revenue vs cost.",
        "Which columns feed the metric that the report mentions?",
        "Who is the author? What did they write?",
        "How does the leave policy relate to the contract terms?",
    ],
)
def test_detects_multi_hop(question):
    assert multihop.is_multi_hop(question)


@pytest.mark.parametrize(
    "question",
    [
        "What is the leave policy?",
        "Summarise the Q3 report.",
        "How many rows are in the sales sheet?",
        "",
    ],
)
def test_leaves_single_hop_alone(question):
    assert not multihop.is_multi_hop(question)


def test_parses_a_numbered_list():
    raw = "1. What is the metric?\n2. Which columns feed it?"
    assert multihop.parse_subquestions(raw, "original") == [
        "What is the metric?",
        "Which columns feed it?",
    ]


def test_parses_bullets_and_drops_preamble():
    raw = "Here are the sub-questions:\n- First lookup?\n* Second lookup?"
    assert multihop.parse_subquestions(raw, "original") == [
        "First lookup?",
        "Second lookup?",
    ]


def test_a_single_subquestion_is_no_hop():
    # Restating the question buys nothing, so it is not treated as multi-hop.
    assert multihop.parse_subquestions("1. Only one thing?", "original") == []


def test_caps_subquestions():
    raw = "\n".join(f"{i}. Question {i}?" for i in range(1, 10))
    assert len(multihop.parse_subquestions(raw, "x")) == multihop.MAX_SUBQUESTIONS


def test_echoing_the_original_is_dropped():
    raw = "1. What is the metric?\n2. What is the metric?\n3. Which columns?"
    parsed = multihop.parse_subquestions(raw, "What is the metric?")
    assert parsed == ["Which columns?"] or parsed == []


def test_seed_keywords_rank_shared_entities_first():
    hop_a = {"data": {"entities": [{"entity_name": "REVENUE"}, {"entity_name": "Q3"}]}}
    hop_b = {"data": {"entities": [{"entity_name": "REVENUE"}, {"entity_name": "Q4"}]}}

    seeds = multihop.seed_keywords([hop_a, hop_b])
    # REVENUE bridges both hops, so it leads.
    assert seeds[0] == "REVENUE"
    assert set(seeds) == {"REVENUE", "Q3", "Q4"}


def test_seed_keywords_survive_junk():
    assert multihop.seed_keywords([]) == []
    assert multihop.seed_keywords([{}, {"data": {}}, None]) == []
    assert multihop.seed_keywords([{"data": {"entities": [{"entity_name": " "}]}}]) == []


def test_seed_keywords_are_capped():
    entities = [{"entity_name": f"E{i}"} for i in range(50)]
    seeds = multihop.seed_keywords([{"data": {"entities": entities}}])
    assert len(seeds) == multihop.MAX_SEED_KEYWORDS


def test_gather_skips_single_hop_questions_entirely():
    class Boom:
        async def aquery_data(self, *a, **kw):
            raise AssertionError("single-hop question must not sub-retrieve")

    seeds = asyncio.run(multihop.gather(Boom(), "What is the leave policy?", dict))
    assert seeds == []


def test_gather_survives_a_failing_sub_retrieval(monkeypatch):
    async def fake_decompose(_):
        return ["First?", "Second?"]

    monkeypatch.setattr(multihop, "decompose", fake_decompose)

    class HalfBroken:
        def __init__(self):
            self.calls = 0

        async def aquery_data(self, question, param=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("retrieval down")
            return {"data": {"entities": [{"entity_name": "SURVIVOR"}]}}

    rag = HalfBroken()
    assert asyncio.run(multihop.gather(rag, "compare a and b", dict)) == ["SURVIVOR"]


def test_decompose_falls_back_when_the_model_fails(monkeypatch):
    import app.services.lightrag_engine as engine

    async def boom(*a, **kw):
        raise RuntimeError("no model")

    monkeypatch.setattr(engine, "llm_model_func", boom)
    assert asyncio.run(multihop.decompose("Compare a and b.")) == []


def test_decompose_honours_the_single_escape_hatch(monkeypatch):
    import app.services.lightrag_engine as engine

    async def single(*a, **kw):
        return "SINGLE"

    monkeypatch.setattr(engine, "llm_model_func", single)
    assert asyncio.run(multihop.decompose("Compare a and b.")) == []
