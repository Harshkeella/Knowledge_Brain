"""Which sentences of an answer the retrieved evidence actually supports.

The answer streams token by token, so an unsupported claim cannot be *removed*
before the user sees it without buffering the whole response and giving up
progressive rendering. So this flags rather than strips: the check runs on the
completed text and the verdict rides out on its own SSE frame, which the UI
attaches to the message.

The test is lexical, not an LLM judge: every content word of a sentence is
looked for in the evidence text. That catches the failure that actually
matters here -- a fluent sentence full of names, numbers and terms that appear
nowhere in what was retrieved -- without a second model call per answer.

ponytail: lexical overlap, so a correct paraphrase that shares few words with
its evidence can be flagged. Swap in an entailment model or an LLM judge here
if the false-positive rate ever bites.
"""

import re

# Below this fraction of a sentence's content words appearing in the evidence,
# the sentence is called unsupported.
SUPPORT_THRESHOLD = 0.5

# A sentence shorter than this is a transition or a lead-in ("Here's why:"),
# which carries no claim to check.
MIN_CONTENT_WORDS = 4

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9]+")

# Function words carry no evidential weight -- a sentence made only of these
# has nothing to verify.
_STOPWORDS = frozenset(
    """a an and are as at be been being but by can could did do does for from
    had has have he her his how i if in into is it its me my no not of on or
    our she should so than that the their them then there these they this
    those to too was we were what when where which who why will with would
    you your it's don't""".split()
)


def _content_words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


def _evidence_text(evidence: list[dict]) -> str:
    parts = []
    for source in evidence or []:
        parts.append(source.get("file_path") or "")
        for step in source.get("chain") or []:
            parts.append(step.get("label") or "")
            parts.append(step.get("snippet") or "")
    return " ".join(parts)


def check(answer: str, evidence: list[dict]) -> dict:
    """`{"checked": n, "unsupported": [sentence, ...], "supported_ratio": f}`.

    With no evidence at all there is nothing to check against, so nothing is
    claimed either way -- a refusal ("the knowledge base has no information on
    this") must not get flagged as an unsupported claim.
    """
    haystack = set(_content_words(_evidence_text(evidence)))
    if not haystack:
        return {"checked": 0, "unsupported": [], "supported_ratio": 1.0}

    checked = 0
    unsupported = []
    for raw in _SENTENCE_SPLIT.split(answer or ""):
        sentence = raw.strip()
        words = _content_words(sentence)
        if len(words) < MIN_CONTENT_WORDS:
            continue
        checked += 1
        hits = sum(1 for w in words if w in haystack)
        if hits / len(words) < SUPPORT_THRESHOLD:
            unsupported.append(sentence)

    return {
        "checked": checked,
        "unsupported": unsupported,
        "supported_ratio": (
            (checked - len(unsupported)) / checked if checked else 1.0
        ),
    }
