"""What `group_splits` must and must not propose.

The must-nots are the point: a merge is far harder to undo than the split it
repaired, so every rule here is about refusing to guess.
"""

from scripts.graph_duplicates import _MAX_CANDIDATES, group_splits


def test_separator_variants_are_one_entity():
    resolved, _ = group_splits(["LIONEL MESSI", "LIONEL_MESSI", "Lionel-Messi"])
    assert resolved == {"LIONEL MESSI": ["LIONEL MESSI", "LIONEL_MESSI", "Lionel-Messi"]}


def test_unsplit_name_is_not_reported():
    resolved, ambiguous = group_splits(["LIONEL MESSI", "DIEGO MARADONA"])
    assert resolved == {} and ambiguous == {}


def test_surname_offers_its_one_full_name():
    _, ambiguous = group_splits(["MESSI", "LIONEL MESSI"])
    assert ambiguous == {"MESSI": ["LIONEL MESSI"]}


def test_two_people_share_a_surname_so_neither_is_chosen():
    _, ambiguous = group_splits(["RONALDO", "CRISTIANO RONALDO", "RONALDO JR"])
    # Listed for a human, with both candidates -- never resolved to one.
    assert ambiguous["RONALDO"] == ["CRISTIANO RONALDO", "RONALDO JR"]


def test_middle_initial_is_a_different_person():
    """The classic false merge: the basketball player and the ML researcher.

    "MICHAEL JORDAN" is a subset of "MICHAEL I JORDAN"'s tokens but not a
    contiguous prefix or suffix of them, which is what keeps the two apart.
    """
    resolved, ambiguous = group_splits(["MICHAEL JORDAN", "MICHAEL I JORDAN"])
    assert resolved == {}
    assert "MICHAEL JORDAN" not in ambiguous


def test_a_name_buried_under_headlines_is_not_a_candidate():
    """Nodes a URL built used to make every real name look ambiguous."""
    headlines = [f"CRISTIANO RONALDO STORY {i}" for i in range(_MAX_CANDIDATES + 1)]
    _, ambiguous = group_splits(["CRISTIANO RONALDO", *headlines])
    assert "CRISTIANO RONALDO" not in ambiguous


def test_empty_and_punctuation_only_names_are_dropped():
    resolved, ambiguous = group_splits(["", "   ", "...", "LIONEL MESSI"])
    assert resolved == {} and ambiguous == {}
