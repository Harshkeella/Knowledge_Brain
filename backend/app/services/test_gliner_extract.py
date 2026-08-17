"""GLiNER extraction: prompt parsing, sentence/window mapping, and the
co-occurrence edges built from it. No model is loaded -- `predict` is faked.
"""

import json

from app.services import gliner_extract as ge

TEXT = (
    "Satya Nadella is the chief executive of Microsoft. "
    "The company later opened an office in Seattle."
)


def _fake_predict(windows: list[str]) -> list[list[dict]]:
    """Locate a few known names by string search, GLiNER's output shape."""
    wanted = {"Satya Nadella": "person", "Microsoft": "organization", "Seattle": "location"}
    out = []
    for window in windows:
        found = []
        for name, label in wanted.items():
            start = window.find(name)
            if start >= 0:
                found.append(
                    {
                        "start": start,
                        "end": start + len(name),
                        "text": name,
                        "label": label,
                        "score": 0.9,
                    }
                )
        out.append(sorted(found, key=lambda e: e["start"]))
    return out


def test_entities_carry_type_and_grounded_description():
    records = ge.extract_records(TEXT, _fake_predict)
    by_name = {e["name"]: e for e in records["entities"]}

    assert set(by_name) == {"Satya Nadella", "Microsoft", "Seattle"}
    assert by_name["Microsoft"]["type"] == "organization"
    # LightRAG drops any entity with an empty description.
    assert by_name["Seattle"]["description"].startswith("The company later opened")


def test_edges_only_join_entities_from_the_same_sentence():
    records = ge.extract_records(TEXT, _fake_predict)
    pairs = {frozenset((r["source"], r["target"])) for r in records["relationships"]}

    assert frozenset(("Satya Nadella", "Microsoft")) in pairs
    # Seattle is in the second sentence -- no edge to the first sentence's names.
    assert frozenset(("Satya Nadella", "Seattle")) not in pairs
    assert frozenset(("Microsoft", "Seattle")) not in pairs


def test_keywords_come_from_the_words_between_the_mentions():
    records = ge.extract_records(TEXT, _fake_predict)
    edge = next(
        r
        for r in records["relationships"]
        if {r["source"], r["target"]} == {"Satya Nadella", "Microsoft"}
    )
    assert edge["keywords"] == "is the chief executive of"


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
