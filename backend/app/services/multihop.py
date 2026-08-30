"""Multi-hop questions, answered in more than one retrieval.

A question like "which columns feed the metric that the Q3 report calls out?"
needs two hops: find the metric, then find its columns. One flat retrieval
embeds the whole sentence and lands between the two, so it tends to surface
the report and miss the columns.

The fix here is deliberately not a new generation path -- the answer still
streams from a single `aquery_llm` call, so nothing about the chat contract
changes. What changes is what that call *searches for*: each sub-question is
retrieved first (`aquery_data`, no LLM), the entities those hops actually
found are collected, and they are seeded into the final call's `ll_keywords`.
LightRAG skips its own keyword extraction when keywords are pre-supplied, so
the last hop searches for hop-1's discoveries by name.

Cost control: the LLM decomposition only runs for questions that trip the
heuristic gate below, so ordinary one-hop questions pay nothing at all.
"""

import logging
import re

logger = logging.getLogger("app.services.multihop")

MAX_SUBQUESTIONS = 3

# Entities carried forward from the early hops. Enough to steer the final
# retrieval, not so many that the seeded keyword list becomes the query.
MAX_SEED_KEYWORDS = 12

# Signals that a question spans more than one fact. Cheap and deliberately
# over-inclusive: a false positive costs one extra retrieval, a false negative
# costs the answer.
_MULTI_HOP_PATTERNS = [
    r"\bcompare[ds]?\b",
    r"\bcomparison\b",
    r"\bdifferences?\s+between\b",
    r"\bversus\b",
    r"\bvs\.?\b",
    r"\bboth\b",
    r"\beach\s+of\b",
    r"\brelationships?\s+between\b",
    r"\bhow\s+.*\brelates?\s+to\b",
    # A chained reference: the object of the question is described by another
    # fact that has to be looked up first.
    r"\bthat\s+(?:the|a|an)\b.*\b(?:mentions?|describes?|defines?|uses?|calls?)\b",
    r"\bwho\s+.*\bthat\b.*\?",
    r"\bwhich\s+.*\bof\s+the\b.*\bthat\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _MULTI_HOP_PATTERNS]


def is_multi_hop(question: str) -> bool:
    """Whether the question looks like it spans more than one retrieval."""
    text = (question or "").strip()
    if not text:
        return False
    if any(pattern.search(text) for pattern in _COMPILED):
        return True
    # Two genuine questions in one message.
    if text.count("?") > 1:
        return True
    return False


def parse_subquestions(raw: str, original: str) -> list[str]:
    """Sub-questions out of the decomposition model's reply.

    Accepts the numbered/bulleted list it is asked for, and tolerates the
    formats models reach for anyway. Returns [] when nothing usable came back,
    which the caller treats as "not multi-hop after all".
    """
    lines = []
    for line in (raw or "").splitlines():
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        # A model that explains itself gets its preamble dropped, not parsed.
        if not cleaned or cleaned.endswith(":"):
            continue
        if cleaned.lower() == original.strip().lower():
            continue
        lines.append(cleaned)
        if len(lines) == MAX_SUBQUESTIONS:
            break
    # One sub-question is just the original question reworded -- no hop gained.
    return lines if len(lines) > 1 else []


async def decompose(question: str) -> list[str]:
    """The question's hops, or [] if it only has one."""
    if not is_multi_hop(question):
        return []

    from app.services.lightrag_engine import llm_model_func

    try:
        raw = await llm_model_func(
            f"Question: {question}\n\nSub-questions:",
            system_prompt=(
                "You split a question into the minimum sequence of simpler "
                f"lookups needed to answer it, at most {MAX_SUBQUESTIONS}. "
                "Reply with one sub-question per line, numbered. No preamble, "
                "no explanation. If the question only needs a single lookup, "
                "reply with exactly: SINGLE"
            ),
        )
        if not isinstance(raw, str) or "SINGLE" in raw.upper():
            return []
        return parse_subquestions(raw, question)
    except Exception:
        # Decomposition is an optimisation. A failure falls back to the single
        # flat retrieval, which is what would have happened anyway.
        logger.warning("Query decomposition failed", exc_info=True)
        return []


def seed_keywords(datas: list[dict]) -> list[str]:
    """Entity names the early hops actually retrieved, most frequent first.

    Frequency ordering matters because the seed list is truncated: an entity
    two hops both found is more likely to be the bridge between them than one
    that appeared once.
    """
    counts: dict[str, int] = {}
    for data in datas:
        for entity in (data or {}).get("data", {}).get("entities", []) or []:
            name = str(entity.get("entity_name") or "").strip()
            if name:
                counts[name] = counts.get(name, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _ in ranked[:MAX_SEED_KEYWORDS]]


async def gather(rag, question: str, param_factory) -> list[str]:
    """Run the early hops and return the keywords to seed the final retrieval.

    `param_factory` builds a fresh QueryParam per sub-question -- QueryParam is
    mutable and LightRAG writes the resolved keywords back onto it, so reusing
    one instance would leak hop 1's keywords into hop 2.
    """
    subquestions = await decompose(question)
    if not subquestions:
        return []

    logger.info("Multi-hop: %d sub-retrievals for %r", len(subquestions), question)

    datas = []
    for sub in subquestions:
        try:
            datas.append(await rag.aquery_data(sub, param=param_factory()))
        except Exception:
            logger.warning("Sub-retrieval failed for %r", sub, exc_info=True)

    return seed_keywords(datas)
