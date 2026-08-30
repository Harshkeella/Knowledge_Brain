from app.services.grounding import check

_EVIDENCE = [
    {
        "file_path": "handbook.pdf",
        "chain": [
            {"type": "source", "label": "handbook.pdf", "snippet": ""},
            {
                "type": "chunk",
                "label": "handbook.pdf",
                "snippet": "Employees accrue 20 days of annual leave per year.",
            },
            {"type": "entity", "label": "ACME", "snippet": "ACME is the employer."},
        ],
    }
]


def test_supported_sentence_passes():
    result = check("Employees accrue 20 days of annual leave per year.", _EVIDENCE)
    assert result["unsupported"] == []
    assert result["supported_ratio"] == 1.0


def test_unsupported_claim_is_flagged():
    answer = (
        "Employees accrue 20 days of annual leave per year. "
        "The chief executive resigned in Lisbon following a currency scandal."
    )
    result = check(answer, _EVIDENCE)
    assert result["checked"] == 2
    assert len(result["unsupported"]) == 1
    assert "Lisbon" in result["unsupported"][0]
    assert result["supported_ratio"] == 0.5


def test_no_evidence_flags_nothing():
    # A refusal must not read as a hallucination.
    result = check("The knowledge base has no information on this.", [])
    assert result == {"checked": 0, "unsupported": [], "supported_ratio": 1.0}


def test_short_transitions_are_not_claims():
    result = check("Here is why:", _EVIDENCE)
    assert result["checked"] == 0
    assert result["unsupported"] == []
