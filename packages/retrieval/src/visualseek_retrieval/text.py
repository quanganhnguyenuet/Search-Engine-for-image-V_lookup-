"""Shared Vietnamese lexical forms: accent-preserving surfaces and folded lookup keys."""

import re
import unicodedata


def surface(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    text = re.sub(
        r"\bh['’]m[oô]ng\b", lambda match: match[0].replace("'", "").replace("’", ""), text
    )
    return " ".join(re.findall(r"[^\W_]+", text))


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    text = re.sub(
        r"\bh['’]m[oô]ng\b", lambda match: match[0].replace("'", "").replace("’", ""), text
    )
    text = unicodedata.normalize("NFD", text.replace("đ", "d"))
    return " ".join(
        re.findall(r"[a-z0-9]+", "".join(char for char in text if not unicodedata.combining(char)))
    )


TONE_MARKS = frozenset("\u0300\u0301\u0303\u0309\u0323")


def accent_key(word: str) -> str:
    """Keep vowel shapes/đ and tone, accepting old/new tone placement (hoà/hòa)."""
    decomposed = unicodedata.normalize("NFD", word.casefold())
    letters = "".join(char for char in decomposed if char not in TONE_MARKS)
    tones = "".join(sorted(char for char in decomposed if char in TONE_MARKS))
    return unicodedata.normalize("NFC", letters) + "|" + tones


def token_matches(word: str, requested: str, *, strict: bool = False) -> bool:
    if normalize(word) != normalize(requested):
        return False
    if not strict and (requested == normalize(requested) or word == normalize(word)):
        return True
    return accent_key(word) == accent_key(requested)


def phrase_position(text: str, query: str) -> int | None:
    words, requested = surface(text).split(), surface(query).split()
    if not requested:
        return None
    # An unmarked syllable in an accented phrase is meaningful: 'bánh chay'
    # must not silently become 'bánh chày'. Entirely ASCII legacy fields remain usable.
    strict = surface(query) != normalize(query) and surface(text) != normalize(text)
    for start in range(len(words) - len(requested) + 1):
        if all(
            token_matches(word, target, strict=strict)
            for word, target in zip(words[start : start + len(requested)], requested, strict=True)
        ):
            return start
    return None
