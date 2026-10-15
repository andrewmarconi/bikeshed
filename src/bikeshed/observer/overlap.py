"""How much of one text comes from another: word shingles, compared in plain code. No model, no I/O."""

import re
from functools import lru_cache
from typing import Literal

SHINGLE = 6     # words per sequence
PARTIAL = 0.5   # share of a text's sequences found in one source for a partial match
THRESHOLDS = (f"_Matches: exact, or partial when at least {PARTIAL:.0%} of a text's {SHINGLE}-word sequences "
              f"appear in one source._")

Kind = Literal["exact", "partial"]


@lru_cache(maxsize=None)
def normalise(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]|_", " ", text.lower()).split())


@lru_cache(maxsize=None)
def shingles(text: str) -> frozenset[tuple[str, ...]]:
    words = normalise(text).split()
    return frozenset(tuple(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1))


def match(text: str, source: str) -> Kind | None:
    """How much of `text` comes from `source`: exact (all of it), partial (at least PARTIAL of its sequences), or None."""
    t, s = normalise(text), normalise(source)
    if not t:
        return None
    if t == s or (len(t.split()) >= SHINGLE and f" {t} " in f" {s} "):
        return "exact"
    own = shingles(text)
    if own and len(own & shingles(source)) / len(own) >= PARTIAL:
        return "partial"
    return None
