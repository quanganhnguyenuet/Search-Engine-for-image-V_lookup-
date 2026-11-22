"""BM25 sparse vectors for Qdrant (IDF is applied server side by ``Modifier.IDF``).

Each word contributes its accent-folded form (``normalize``) and, when it carries Vietnamese
marks, its accented surface form too, so "banh chay" matches both spellings while
"bánh chay" still prefers documents that spell it the same way.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass

from visualseek_retrieval.text import normalize, surface

K1 = 1.2
B = 0.75


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for word in surface(text).split():
        folded = normalize(word)
        if not folded:
            continue
        out.extend(folded.split())
        if word != folded:
            out.append(f"~{word}")
    return out


def token_id(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(), "big")


@dataclass(frozen=True)
class SparseVector:
    indices: list[int]
    values: list[float]


def _merge(weights: dict[int, float]) -> SparseVector:
    items = sorted(weights.items())
    return SparseVector([index for index, _ in items], [value for _, value in items])


def document_vector(text: str, avg_length: float) -> SparseVector:
    counts = Counter(tokens(text))
    length = sum(counts.values())
    weights: dict[int, float] = {}
    for token, tf in counts.items():
        weight = tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / avg_length))
        index = token_id(token)
        weights[index] = weights.get(index, 0.0) + weight
    return _merge(weights)


def query_vector(text: str) -> SparseVector:
    return _merge({token_id(token): 1.0 for token in set(tokens(text))})
