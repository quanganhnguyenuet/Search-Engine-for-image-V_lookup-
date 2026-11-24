"""Vietnamese connective words: which ones join intents, which ones exclude, which are noise.

One inventory for the whole query path. Every cue has an accented pattern (used when the
query carries diacritics) and a folded pattern (used when it does not). Folded patterns keep
only spellings that are not also common product words: folded "kem" is also "kem" (cream),
"voi" is also "voi" (elephant), "nhung" is also "nhung" (velvet), "hay" is also "hay" (good).

Kinds
  and       joins two things the user wants: "và", "cùng với", "lẫn", "cũng như", commas
  or        "hoặc", "hay là": also two intents for ranking purposes
  additive  discourse markers that add an intent: "ngoài ra", "bên cạnh đó", "đồng thời"
  contrast  "nhưng", "tuy nhiên": a new clause, often followed by an exclusion
  except    exclusion of what follows: "trừ", "ngoại trừ", "không phải", "không lấy", ...
  besides   "ngoài X (ra|thì)": exclusion of X, not to be confused with "ngoài ra"

"với" and "kèm" are deliberately not intent separators: "bàn phím với đèn led" is one
object with an attribute, not two objects.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from visualseek_retrieval.text import normalize, surface


def _compile(words: str) -> re.Pattern:
    return re.compile(rf"(?<!\w)({words})(?!\w)")


# kind -> (accented, folded). Alternatives are tried left to right, so longer ones come first;
# kinds are tried in dict order and an earlier kind wins an overlap.
CONNECTORS: dict[str, tuple[re.Pattern, re.Pattern]] = {
    "additive": (
        _compile("ngoài ra|bên cạnh đó|đồng thời|thêm vào đó|không chỉ"),
        _compile("ngoai ra|ben canh do|dong thoi|them vao do|khong chi"),
    ),
    "except": (
        _compile(
            "ngoại trừ|không bao gồm|không gồm|không lấy|không cần|không phải|chứ không|"
            "loại trừ|loại bỏ|trừ"
        ),
        _compile(
            "ngoai tru|khong bao gom|khong gom|khong lay|khong can|khong phai|chu khong|"
            "loai tru|loai bo"
        ),
    ),
    "besides": (
        _compile(r"ngoài(?!\s+ra(?!\w))"),
        _compile(r"ngoai(?!\s+ra(?!\w))"),
    ),
    "contrast": (_compile("nhưng|tuy nhiên|song"), _compile("tuy nhien")),
    "or": (_compile("hoặc là|hoặc|hay là|hay"), _compile("hoac la|hoac|hay la")),
    "and": (
        _compile("cùng với|cũng như|lẫn|và"),
        _compile("cung voi|cung nhu|va"),
    ),
}
# Kinds that end one intent and start the next one.
SEPARATORS = ("additive", "contrast", "or", "and")
PUNCTUATION = re.compile(r"[,;+&/]")
_MARK = "zzsepzz"


@dataclass(frozen=True)
class Cue:
    kind: str
    text: str
    start: int  # character offsets in the normalised query text
    end: int


def _text(query: str) -> tuple[str, bool]:
    accented = surface(query) != normalize(query)
    return (surface(query) if accented else normalize(query)), accented


def find(query: str) -> list[Cue]:
    """Every connective cue, left to right; overlapping matches keep the earlier kind."""
    text, accented = _text(query)
    found: list[Cue] = []
    taken: list[tuple[int, int]] = []
    for kind, patterns in CONNECTORS.items():  # dict order = priority on overlaps
        for match in patterns[0 if accented else 1].finditer(text):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in taken):
                continue
            taken.append(span)
            found.append(Cue(kind, match.group(), *span))
    return sorted(found, key=lambda cue: cue.start)


def segments(query: str) -> list[str]:
    """The query split at intent separators and list punctuation (empty parts dropped)."""
    # Punctuation is dropped by the normalisers, so mark it with a placeholder word first.
    marked = PUNCTUATION.sub(f" {_MARK} ", unicodedata.normalize("NFC", query))
    text, _ = _text(marked)
    cuts = [c for c in find(text) if c.kind in SEPARATORS]
    parts, last = [], 0
    for cue in cuts:
        parts.append(text[last : cue.start])
        last = cue.end
    parts.append(text[last:])
    out = []
    for part in parts:
        for piece in part.split(_MARK):
            piece = " ".join(piece.split())
            if piece:
                out.append(piece)
    return out


def has_negation(query: str) -> Cue | None:
    """The first exclusion cue ("trừ", "không phải", "ngoài X ra"), not "ngoài ra"."""
    return next((c for c in find(query) if c.kind in ("except", "besides")), None)
