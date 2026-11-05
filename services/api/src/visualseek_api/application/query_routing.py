"""Cheap query routing; these rules select a path, not a semantic correctness verdict."""

from visualseek_retrieval.hybrid import normalize
from visualseek_retrieval.text import surface

PREFIXES = (
    "toi muon tim hinh anh ve", "toi muon tim hinh anh", "toi muon tim anh",
    "cho toi xem hinh anh", "cho toi xem anh", "cho toi hinh anh", "cho toi anh",
    "tim hinh anh ve", "tim hinh anh", "tim anh ve", "tim anh", "hinh anh ve",
    "hinh anh", "toi can tim", "toi muon tim", "toi can anh", "hay tim", "anh ve",
    "cho toi xem", "tim", "anh",
)
LOGIC_PHRASES = (
    "khong", "ngoai tru", "loai tru", "nhung", "hoac", "va", "hay la",
    "it nhat", "toi da", "it hon", "nhieu hon", "lon hon", "nho hon", "so voi",
    "hai nhom", "moi nhom", "tung nhom", "nhom rieng", "rieng biet", "tach rieng",
    "huong dan", "day tre", "day hoc", "con lai", "nguoi kia",
    "without", "excluding", "except", "not", "and", "or", "each group", "two groups",
    "at least", "at most", "teaching", "guiding",
)


def search_term(query: str, *, preserve_accents: bool = False) -> str:
    words = surface(query).split()
    while True:
        for prefix in PREFIXES:
            parts = prefix.split()
            if len(words) > len(parts) and [normalize(w) for w in words[:len(parts)]] == parts:
                words = words[len(parts):]
                break
        else:
            term = " ".join(words)
            return term if preserve_accents else normalize(term)


def needs_query_planning(query: str) -> tuple[bool, str]:
    if ";" in query or "," in query or len(query.strip().splitlines()) > 1:
        return True, "multiple_query_clauses"
    padded = f" {search_term(query)} "
    if any(f" {phrase} " in padded for phrase in LOGIC_PHRASES):
        return True, "logical_or_multi_clause_query"
    # Length alone does not establish multiple intents or a need for reasoning.
    return False, "direct_retrieval_query"
