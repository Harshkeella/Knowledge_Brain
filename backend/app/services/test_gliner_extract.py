"""GLiNER extraction: prompt parsing, sentence/window mapping, and the
co-occurrence edges built from it. No model is loaded -- `predict` is faked.
"""

import json
import re

from app.services import gliner_extract as ge
from app.services import graph_schema

TEXT = (
    "Satya Nadella is the chief executive of Microsoft. "
    "The company later opened an office in Seattle."
)


def _fake_predict(windows: list[str]) -> list[list[dict]]:
    """Locate every mention of a few known names, GLiNER's output shape.

    Case-insensitive and all occurrences, like the real model: that is what
    puts the same pair in several sentences and the same name in several
    casings, which is exactly what the graph must not duplicate.
    """
    wanted = {"Satya Nadella": "person", "Microsoft": "organization", "Seattle": "location"}
    out = []
    for window in windows:
        found = []
        for name, label in wanted.items():
            for match in re.finditer(re.escape(name), window, re.IGNORECASE):
                found.append(
                    {
                        "start": match.start(),
                        "end": match.end(),
                        "text": match.group(),
                        "label": label,
                        "score": 0.9,
                    }
                )
        out.append(sorted(found, key=lambda e: e["start"]))
    return out


def test_entities_carry_type_and_grounded_description():
    records = ge.extract_records(TEXT, _fake_predict)
    by_name = {e["name"]: e for e in records["entities"]}

    # Canonical names: one node per thing, whatever the casing in the prose.
    assert set(by_name) == {"SATYA NADELLA", "MICROSOFT", "SEATTLE"}
    assert by_name["MICROSOFT"]["type"] == "organization"
    # LightRAG drops any entity with an empty description.
    assert by_name["SEATTLE"]["description"].startswith("The company later opened")


def test_edges_only_join_entities_from_the_same_sentence():
    records = ge.extract_records(TEXT, _fake_predict)
    pairs = {frozenset((r["source"], r["target"])) for r in records["relationships"]}

    assert frozenset(("SATYA NADELLA", "MICROSOFT")) in pairs
    # Seattle is in the second sentence -- no edge to the first sentence's names.
    assert frozenset(("SATYA NADELLA", "SEATTLE")) not in pairs
    assert frozenset(("MICROSOFT", "SEATTLE")) not in pairs


def test_case_variants_of_one_name_are_a_single_entity():
    text = "Microsoft shipped it. microsoft grew. The Microsoft's revenue rose."
    records = ge.extract_records(text, _fake_predict)

    assert [e["name"] for e in records["entities"]] == ["MICROSOFT"]


def test_edges_are_typed_and_never_repeat_a_pair():
    text = (
        "Satya Nadella runs Microsoft. Satya Nadella founded Microsoft again. "
        "Satya Nadella and Microsoft agreed."
    )
    records = ge.extract_records(text, _fake_predict)

    # Three sentences, one pair, one edge -- carrying the closed vocabulary's
    # type rather than three different snippets of connecting prose.
    assert len(records["relationships"]) == 1
    assert records["relationships"][0]["keywords"] == graph_schema.RELATED_TO


def test_long_text_is_split_into_multiple_windows():
    long_text = " ".join(f"Sentence number {i} mentions Microsoft." for i in range(200))
    seen: list[int] = []

    def counting_predict(windows):
        seen.append(len(windows))
        return _fake_predict(windows)

    ge.extract_records(long_text, counting_predict)
    assert seen[0] > 1
    assert all(len(w) <= ge._WINDOW_CHARS + 200 for _, w in ge._windows(long_text, ge._split_sentences(long_text)))


def test_prompt_round_trip_survives_a_chunk_containing_code_fences():
    chunk = "Microsoft ships code.\n```python\nprint('hi')\n```\nSeattle is the office."
    prompt = f"---Input Text---\n```\n{chunk}\n```\n\n---Output---\n"
    assert ge._INPUT_TEXT_RE.search(prompt).group(1) == chunk


def test_gleaning_prompt_without_input_text_returns_empty_json():
    import asyncio

    result = asyncio.run(ge.gliner_extract("---Task---\nBased on the last extraction"))
    assert json.loads(result) == {"entities": [], "relationships": []}


def test_summary_prompt_returns_merged_prose_not_extraction_json():
    import asyncio

    jsonl = "\n".join(
        json.dumps({"Description": d})
        for d in ["Microsoft ships Copilot.", "Microsoft ships Copilot.", "It is based in Redmond."]
    )
    prompt = f"Description List:\n\n```\n{jsonl}\n```\n\n---Output---\n"

    result = asyncio.run(ge.gliner_extract(prompt))

    # Duplicates collapse, and the result is prose -- feeding an entity
    # description `{"entities": []}` is what the summary branch exists to stop.
    assert result == "Microsoft ships Copilot. It is based in Redmond."


def test_spreadsheet_summaries_are_skipped_entirely():
    """Their structure is written into the graph deterministically; extracting
    it again from prose is what put `VARCHAR` and `BIGINT` in the graph."""
    import asyncio

    from app.services.parsers.spreadsheet import SUMMARY_HEADER

    body = f"{SUMMARY_HEADER} sales.xlsx\nWorksheet 'Q1': 4 rows, 3 columns."
    prompt = f"---Input Text---\n```\n{body}\n```\n\n---Output---\n"

    assert asyncio.run(ge.gliner_extract(prompt)) == ge.EMPTY_RESULT
