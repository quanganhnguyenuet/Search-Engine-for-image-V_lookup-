"""Fast intent classification learned from examples; no provider call for routing."""

import json
import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from visualseek_retrieval.hybrid import normalize
from visualseek_retrieval.text import surface

PREFIXES = (
    "toi muon tim hinh anh ve", "toi muon tim hinh anh", "toi muon tim anh",
    "cho toi xem hinh anh", "cho toi xem anh", "cho toi hinh anh", "cho toi anh",
    "tim hinh anh ve", "tim hinh anh", "tim anh ve", "tim anh", "hinh anh ve",
    "hinh anh", "toi can tim", "toi muon tim", "toi can anh", "hay tim", "anh ve",
    "cho toi xem", "tim", "anh",
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


def intent_features(query: str) -> Counter:
    """Keep accents: trừ/trụ and những/nhưng are different observations."""
    words = surface(query).split()
    features = Counter({'word:' + word: 1 for word in words})
    features.update({'pair:' + a + ' ' + b: 1 for a, b in zip(words, words[1:], strict=False)})
    for word in sorted(set(words)):
        padded = '^' + word + '$'
        features.update({'char:' + padded[i:i + 3]: 0.15 for i in range(len(padded) - 2)})
    return features


@dataclass(frozen=True)
class IntentDecision:
    needs_reasoning: bool
    reason: str
    score: float


class IntentRouter:
    def __init__(self):
        path = Path(__file__).with_name('query_router_v1.json')
        model = json.loads(path.read_text(encoding='utf-8'))
        self.version = model['version']
        self.weights = model['weights']
        self.idf = model['idf']
        self.bias = model['bias']
        self.local_threshold = model['local_threshold']
        self.reasoning_threshold = model['reasoning_threshold']
        self.classify = lru_cache(maxsize=256)(self._classify)

    def _classify(self, query: str) -> IntentDecision:
        features = intent_features(query)
        values = {key: value * self.idf[key] for key, value in features.items() if key in self.idf}
        norm = math.sqrt(sum(value ** 2 for value in values.values()))
        score = self.bias + sum(self.weights[key] * value for key, value in values.items()) / max(
            norm, 1e-12)
        if score <= self.local_threshold:
            return IntentDecision(False, 'direct_visual_request', score)
        reason = ('reasoning_intent' if score >= self.reasoning_threshold
                  else 'uncertain_intent')
        return IntentDecision(True, reason, score)
