"""Shared Vietnamese lexical forms: accent-preserving surfaces and folded lookup keys."""

import re
import unicodedata


def surface(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFC", text).casefold()))


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    return " ".join(re.findall(r"[a-z0-9]+", "".join(
        char for char in text if not unicodedata.combining(char)
    )))
