from app.services.provenance import MAX_PER_KIND, SNIPPET_CHARS, build_evidence

_DATA = {
    "references": [
        {"reference_id": "1", "file_path": "handbook.pdf"},
        {"reference_id": "2", "file_path": "notes.md"},
        {"reference_id": "3", "file_path": "orphan.txt"},
    ],
    "chunks": [
        {"chunk_id": "c1", "reference_id": "1", "file_path": "handbook.pdf",
         "content": "Leave   policy\nis  20 days."},
    ],
    "entities": [
        {"entity_name": "ACME", "entity_type": "organization",
         "description": "A company.", "reference_id": "1"},
        {"entity_name": "LEAVE POLICY", "entity_type": "concept",
         "description": "Time off rules.", "reference_id": "2"},
        # reference_id that isn't in the reference list -- unattributable.
        {"entity_name": "GHOST", "reference_id": "99", "description": "x"},
    ],
    "relationships": [
        {"src_id": "ACME", "tgt_id": "LEAVE POLICY", "keywords": "RELATED_TO",
         "description": "ACME defines it.", "reference_id": "1"},
    ],
}


def test_groups_per_source_and_orders_the_chain():
    evidence = build_evidence(_DATA)

    # orphan.txt contributed nothing, so it gets no panel.
    assert [e["reference_id"] for e in evidence] == ["1", "2"]

    first = evidence[0]
    assert first["file_path"] == "handbook.pdf"
    assert [s["type"] for s in first["chain"]] == [
        "source",
        "chunk",
        "entity",
        "relationship",
    ]
    # Whitespace collapsed, so the panel doesn't render raw newlines.
    assert first["chain"][1]["snippet"] == "Leave policy is 20 days."
    rel = first["chain"][3]
    assert rel["label"] == "ACME → LEAVE POLICY"
    # Both endpoints survive, so each is separately deep-linkable.
    assert (rel["src_id"], rel["tgt_id"]) == ("ACME", "LEAVE POLICY")

    assert [s["id"] for s in evidence[1]["chain"]] == ["notes.md", "LEAVE POLICY"]


def test_unattributable_items_are_dropped():
    names = [
        s["id"]
        for e in build_evidence(_DATA)
        for s in e["chain"]
    ]
    assert "GHOST" not in names


def test_snippets_are_truncated():
    data = {
        "references": [{"reference_id": "1", "file_path": "big.txt"}],
        "chunks": [
            {"chunk_id": "c", "reference_id": "1", "content": "word " * 500}
        ],
    }
    snippet = build_evidence(data)[0]["chain"][1]["snippet"]
    assert len(snippet) == SNIPPET_CHARS
    assert snippet.endswith("…")


def test_caps_steps_per_kind():
    data = {
        "references": [{"reference_id": "1", "file_path": "wide.txt"}],
        "entities": [
            {"entity_name": f"E{i}", "reference_id": "1", "description": "d"}
            for i in range(MAX_PER_KIND + 20)
        ],
    }
    chain = build_evidence(data)[0]["chain"]
    assert len([s for s in chain if s["type"] == "entity"]) == MAX_PER_KIND


def test_empty_input_is_not_an_error():
    assert build_evidence({}) == []
    assert build_evidence(None) == []
