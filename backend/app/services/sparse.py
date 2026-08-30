"""BM25-style sparse vectors for Qdrant's keyword half of hybrid search.

Qdrant computes IDF itself (the collection's sparse vector uses
``Modifier.IDF``), so all this has to produce is term ids and term
frequencies. That makes the encoder a tokenizer plus a hash -- no corpus
statistics to keep in sync with the index, and no model to download.
"""

import re
from hashlib import blake2b

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.\-]*")

# Only the words that appear in nearly every English document. A short list on
# purpose: with IDF weighting, a common word is already worth almost nothing,
# so an aggressive stopword list only risks dropping a real query term.
_STOPWORDS = frozenset(
    """a an and are as at be by for from has have he how i in is it its of on or
    that the this to was were what when where which who will with you your""".split()
)

# BM25 term-frequency saturation. Length normalization (the `b` term) needs the
# corpus average document length, which would have to be maintained alongside
# the index; k1 saturation alone is what keeps a term repeated 50 times from
# dominating, and that is the part that matters here.
_K1 = 1.5

# ponytail: no stemming, so "invoices" and "invoice" are different terms. Add
# py-rust-stemmers to _term() if recall on inflected words proves short.


def _term_id(token: str) -> int:
    """Stable uint32 id for a token. Collisions are ~1 in 4 billion per pair;
    a collision costs a little precision, never correctness."""
    return int.from_bytes(blake2b(token.encode("utf-8"), digest_size=4).digest(), "big")


def tokenize(text: str) -> list[str]:
    return [
        t
        for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOPWORDS and len(t) > 1
    ]


def encode_document(text: str) -> tuple[list[int], list[float]]:
    """(indices, values) for indexing: term ids and saturated frequencies."""
    counts: dict[int, int] = {}
    for token in tokenize(text):
        term = _term_id(token)
        counts[term] = counts.get(term, 0) + 1
    indices = sorted(counts)
    return indices, [counts[i] / (counts[i] + _K1) for i in indices]


def encode_query(text: str) -> tuple[list[int], list[float]]:
    """(indices, values) for searching. Every query term weighs the same --
    Qdrant's IDF modifier is what makes the rare ones count."""
    indices = sorted({_term_id(t) for t in tokenize(text)})
    return indices, [1.0] * len(indices)


if __name__ == "__main__":
    doc_i, doc_v = encode_document("Invoice INV-2024-017 for Acme Corp: the total is 4200")
    assert _term_id("acme") in doc_i, "content words must survive tokenization"
    assert _term_id("the") not in doc_i, "stopwords must be dropped"
    assert _term_id("inv-2024-017") in doc_i, "identifiers must stay one term"
    assert doc_i == sorted(doc_i), "Qdrant requires ascending sparse indices"
    assert all(0 < v < 1 for v in doc_v), "saturated tf is bounded"

    rep_i, rep_v = encode_document("acme " * 50)
    assert rep_v[rep_i.index(_term_id("acme"))] < 1.0, "tf must saturate, not grow"

    q_i, q_v = encode_query("What is the Acme total?")
    assert set(q_i) & set(doc_i), "query and document share terms"
    assert q_v == [1.0] * len(q_i)
    assert encode_document("") == ([], []), "empty text yields an empty vector"
    print("ok")
