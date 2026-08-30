"""Local, ontology-constrained entity/relationship extraction for LightRAG.

Drop-in for the "extract" role LLM: LightRAG hands us its JSON extraction
prompt, we pull the chunk text back out, run a local GLiNER encoder over it,
and return the same JSON shape the LLM would have. Nothing downstream changes
-- LightRAG still does the parsing, merging, graph upsert and vector writes.

Why not just tune the LLM path: ingest speed there is bound by Groq's
tokens-per-minute budget, not model speed (~2 chunks/minute at MAX_ASYNC_LLM=2
against a 12,000 TPM ceiling), so a 200-page book never finishes and fails on
quota instead. GLiNER is one forward pass per window, local, no quota, no 429.

The ontology (ENTITY_LABELS) is what the model is scored against on every
chunk, so entity types stay consistent across a document instead of the LLM
inventing "artifact" in one chunk and "tool" in the next.
"""

import asyncio
import bisect
import json
import logging
import re
import threading
from typing import Callable

from app.core.config import get_settings
from app.services import graph_schema
from app.services.parsers.spreadsheet import SUMMARY_HEADER

logger = logging.getLogger("app.gliner_extract")

_settings = get_settings()

EMPTY_RESULT = '{"entities": [], "relationships": []}'

# The chunk text as LightRAG's entity_extraction_json_user_prompt embeds it.
# Greedy so a chunk that itself contains ``` fences still matches to the real
# closing fence, and anchored on ---Output--- so it can't run past the prompt.
_INPUT_TEXT_RE = re.compile(
    r"---Input Text---\n```\n(.*)\n```\n\n---Output---", re.DOTALL
)

# LightRAG routes description summarization (fired when an entity accumulates
# FORCE_LLM_SUMMARY_ON_MERGE fragments) through the SAME "extract" role, so
# taking that role over means taking this over too -- otherwise every popular
# entity in a book gets `{"entities": [], ...}` as its description.
_DESCRIPTION_LIST_RE = re.compile(
    r"Description List:\n\n```\n(.*)\n```\n\n---Output---", re.DOTALL
)
_MAX_SUMMARY_CHARS = 1200

# Sentences keep their trailing punctuation/newline so offsets stay contiguous
# with the source text -- entity offsets are mapped back through these.
_SENTENCE_RE = re.compile(r"[^.!?\n]*[.!?\n]+|[^.!?\n]+$")

# A reference section is URL soup, and GLiNER scores the slug in
# ".../story/4805137/cristiano-ronaldo-becomes-first-man-to-score-in-5-world-cups"
# as a `person`. One Wikipedia article put 158 such nodes in the graph -- and
# because each carries the real surname as a token, they also make "RONALDO"
# genuinely ambiguous, which is what blocks alias resolution from ever firing.
# So this is upstream of the duplicate-node bug, not a separate cleanup.
#
# Masked with spaces rather than deleted: every entity offset below is an index
# into this string, so the replacement has to be the same length as the match.
# A chunk boundary can cut a URL in half, so the tail arrives as a bare
# "au/football/.../cristiano-ronaldo-conundrum-...". Matching those needs the
# TLD spelled out: a generic `\w+/\S+` would eat "src/app/page.tsx" and every
# other real path in a code or docs ingest.
_TLD = (
    "com|org|net|edu|gov|mil|int|io|ai|co|me|tv|info|biz"
    "|uk|de|fr|es|it|pt|nl|be|ch|at|se|no|dk|fi|pl|ru|ua|gr|cz|ro|hu|ie"
    "|ca|au|nz|jp|cn|kr|in|br|mx|ar|cl|za|sa|ae|tr|il|sg|hk|tw|id|my|th|ph"
)
_URL_RE = re.compile(
    rf"""https?://\S+
      | www\.\S+
      | \b[\w.-]*\w\.(?:{_TLD})\b/\S*
      | \b(?:{_TLD})/\S+""",
    re.IGNORECASE | re.VERBOSE,
)


def _mask_urls(text: str) -> str:
    """Blank out URLs, preserving length so entity offsets stay valid."""
    return _URL_RE.sub(lambda m: " " * len(m.group()), text)


def _clean(sentence: str) -> str:
    """Evidence text for a node/edge description. Masking leaves long runs of
    spaces where a citation was; collapse them so the description reads."""
    return re.sub(r"\s+", " ", sentence).strip()


# GLiNER's encoder truncates past ~384 tokens, so a 1024-token chunk has to be
# windowed. Bigger windows are faster (fewer forward passes) but hit that
# ceiling; ~900 chars (~150 words) sits comfortably under it.
_WINDOW_CHARS = 900

# Per-chunk output caps, mirroring the limits the LLM prompt asks for. Without
# them a dense sentence with 8 entities alone contributes 28 edges.
_MAX_ENTITIES_PER_CHUNK = 40
_MAX_RELATIONS_PER_CHUNK = 60
_MAX_ENTITIES_PER_SENTENCE = 5
_MAX_DESCRIPTION_CHARS = 400


def _split_sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) char spans covering `text`, one per sentence."""
    return [(m.start(), m.end()) for m in _SENTENCE_RE.finditer(text) if m.group().strip()]


def _windows(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, str]]:
    """Group sentences into (start_offset, window_text) chunks under the
    encoder's length ceiling. A single over-long sentence becomes its own
    window and is truncated by the model rather than dropped."""
    out: list[tuple[int, str]] = []
    start = end = None
    for s, e in spans:
        if start is None:
            start, end = s, e
        elif e - start > _WINDOW_CHARS:
            out.append((start, text[start:end]))
            start, end = s, e
        else:
            end = e
    if start is not None:
        out.append((start, text[start:end]))
    return out


def extract_records(text: str, predict: Callable[[list[str]], list[list[dict]]]) -> dict:
    """Entities + relationships for one chunk, in LightRAG's JSON schema.

    `predict` takes a batch of window texts and returns per-window entity dicts
    (start/end/text/label/score) -- injected so the logic is testable without
    loading a model.
    """
    text = _mask_urls(text)
    spans = _split_sentences(text)
    windows = _windows(text, spans)
    if not windows:
        return {"entities": [], "relationships": []}

    sentence_starts = [s for s, _ in spans]

    # canonical name -> record. The name IS the node identity: LightRAG merges
    # entities by exact string, so folding case/articles/possessives here is
    # what stops "Microsoft", "microsoft" and "the Microsoft's" from becoming
    # three nodes for one company -- in this chunk and in every other one,
    # since the fold is deterministic rather than first-seen.
    entities: dict[str, dict] = {}
    # sentence index -> [name] in order of appearance
    by_sentence: dict[int, list[str]] = {}

    for (offset, window), found in zip(windows, predict([w for _, w in windows])):
        for ent in found:
            name = graph_schema.canonical_name(ent["text"])
            if not name:
                continue
            si = bisect.bisect_right(sentence_starts, offset + ent["start"]) - 1
            sentence = _clean(text[spans[si][0] : spans[si][1]]) if si >= 0 else ""

            if name not in entities:
                entities[name] = {
                    "name": name,
                    # Canonical label casing, so the property graph's legend
                    # never splits one type into `person` and `Person`.
                    "type": graph_schema.canonical_label(ent["label"]),
                    # GLiNER returns no description, and LightRAG drops any
                    # entity without one. The sentence it was found in is both
                    # non-empty and genuinely grounded context for retrieval.
                    "description": sentence[:_MAX_DESCRIPTION_CHARS] or name,
                    "_score": ent.get("score", 1.0),
                }
            if si >= 0:
                mentions = by_sentence.setdefault(si, [])
                if name not in mentions:
                    mentions.append(name)

    kept = sorted(entities.values(), key=lambda e: -e["_score"])[:_MAX_ENTITIES_PER_CHUNK]
    kept_names = {e["name"] for e in kept}

    # Relationships are same-sentence co-occurrence, and their type is the
    # closed vocabulary's RELATED_TO -- not the words between the two mentions.
    # Those words were unbounded free text, so a pair seen in ten sentences
    # produced ten edges with ten different "types" that LightRAG then merged
    # into one comma-salad keyword; the sentence is already the edge's
    # description, so nothing is lost by typing the edge properly. One edge per
    # unordered pair per chunk, keeping the first sentence as its evidence.
    #
    # ponytail: no relation model, so every prose edge is RELATED_TO. Swap in
    # GLiREL (or a Tier-2 LLM pass) if the graph needs real verb types.
    relationships: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    for si, mentions in sorted(by_sentence.items()):
        if len(relationships) >= _MAX_RELATIONS_PER_CHUNK:
            break
        sentence = _clean(text[spans[si][0] : spans[si][1]])
        mentions = [m for m in mentions if m in kept_names][:_MAX_ENTITIES_PER_SENTENCE]
        for i, a_name in enumerate(mentions):
            for b_name in mentions[i + 1 :]:
                pair = (a_name, b_name) if a_name < b_name else (b_name, a_name)
                if pair[0] == pair[1] or pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                relationships.append(
                    {
                        "source": a_name,
                        "target": b_name,
                        "keywords": graph_schema.RELATED_TO,
                        "description": sentence[:_MAX_DESCRIPTION_CHARS],
                    }
                )
                if len(relationships) >= _MAX_RELATIONS_PER_CHUNK:
                    break
            if len(relationships) >= _MAX_RELATIONS_PER_CHUNK:
                break

    for e in kept:
        e.pop("_score", None)
    return {"entities": kept, "relationships": relationships}


def summarize_descriptions(jsonl: str) -> str:
    """Merge an entity's accumulated descriptions without an LLM.

    ponytail: these descriptions are verbatim source sentences, not model
    prose, so de-duplicating and joining them keeps every fact an LLM summary
    would have preserved -- at zero tokens. Route this back to llm_model_func
    if the graph needs genuinely abstractive summaries.
    """
    seen: list[str] = []
    for line in jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            description = json.loads(line).get("Description", "")
        except (json.JSONDecodeError, AttributeError):
            description = line
        description = description.strip()
        if description and description not in seen:
            seen.append(description)

    merged = " ".join(seen)
    return merged[:_MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] if len(merged) > _MAX_SUMMARY_CHARS else merged


_model = None
# Chunks are extracted from a thread pool, so two of them can race the first
# load and pull the weights into memory twice.
_model_lock = threading.Lock()


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from gliner import GLiNER

            logger.info("Loading GLiNER extraction model %s", _settings.gliner_model)
            model = GLiNER.from_pretrained(_settings.gliner_model).eval()
            _model = model.to("cuda") if _settings.gliner_use_gpu else model
    return _model


def _predict(windows: list[str]) -> list[list[dict]]:
    return _get_model().inference(
        windows,
        _settings.entity_labels,
        threshold=_settings.gliner_threshold,
        batch_size=_settings.gliner_batch_size,
    )


async def gliner_extract(
    prompt,
    system_prompt=None,
    history_messages=None,
    keyword_extraction=False,
    **kwargs,
) -> str:
    """LightRAG "extract" role func. Signature matches the LLM funcs it replaces.

    The role serves two prompts: chunk extraction and description summarization.
    """
    match = _INPUT_TEXT_RE.search(prompt)
    if match is None:
        summary = _DESCRIPTION_LIST_RE.search(prompt)
        if summary is not None:
            return summarize_descriptions(summary.group(1))
        # The gleaning ("continue extraction") prompt carries no input text --
        # there is nothing for a single-pass model to add on a second look.
        return EMPTY_RESULT

    text = match.group(1)
    # A spreadsheet's structure is written into the graph node by node by
    # tabular_graph, from what the parser actually read. Running the extractor
    # over the summary as well only produced a second, worse copy of it --
    # `VARCHAR`, `BIGINT` and `categorical` as entities, wired to the column
    # names. The deterministic version is the one that is right.
    if text.lstrip().startswith(SUMMARY_HEADER):
        return EMPTY_RESULT

    records = await asyncio.to_thread(extract_records, text, _predict)
    return json.dumps(records, ensure_ascii=False)


def warmup() -> None:
    """Load the model at boot so the first ingest doesn't pay for it."""
    _get_model()
