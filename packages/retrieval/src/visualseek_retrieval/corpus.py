"""Ground local requests in indexed titles/tags, independently of evaluation labels."""

import re
from collections import defaultdict
from functools import lru_cache

from visualseek_retrieval.text import normalize, phrase_position, surface

# Everyday equivalent names; not learned from query judgments or retrieval scores.
ALIASES = (
    ("dưa leo", "dưa chuột"), ("đậu phộng", "lạc"), ("thịt heo", "thịt lợn"),
    ("hồ gươm", "hồ hoàn kiếm"), ("vịnh hạ long", "hạ long bay", "biển hạ long"),
)


class CorpusTerms:
    def __init__(self, assets: list[dict]):
        self.labels = defaultdict(set)
        self.tag_labels = defaultdict(set)
        self.token_labels = defaultdict(set)
        self.trie = {}
        titles = set()
        for position, asset in enumerate(assets):
            if asset.get("search_excluded"):
                continue
            fields = [asset.get("keyword", ""), *asset.get("tags", [])]
            for field_index, field in enumerate(fields):
                label = surface(str(field))
                if label:
                    self.labels[label].add(position)
                    if field_index:
                        self.tag_labels[label].add(position)
                    else:
                        titles.add(label)
        for label in self.labels:
            for token in set(normalize(label).split()):
                self.token_labels[token].add(label)
            # Require repeated tag evidence; titles are independently valid names.
            if label in {"người", "nước", "cây", "ảnh", "hình", "đồ", "vật"}:
                continue
            if len(self.labels[label]) < 2 and label not in titles:
                continue
            node = self.trie
            for word in normalize(label).split():
                node = node.setdefault(word, {})
            node.setdefault("", []).append(label)
        self.positions = lru_cache(maxsize=512)(self._positions)

    def mentions(self, query: str) -> bool:
        words = surface(query).split()
        folded = [normalize(word) for word in words]
        for start in range(len(words)):
            node = self.trie
            for end in range(start, len(words)):
                node = node.get(folded[end])
                if node is None:
                    break
                phrase = " ".join(words[start:end + 1])
                if any(phrase_position(label, phrase) is not None for label in node.get("", [])):
                    return True
        return False

    def _positions(self, term: str, tags_only: bool = False) -> frozenset[int]:
        term = surface(term)
        forms = next((group for group in ALIASES if term in group), (term,))
        result = set()
        for form in forms:
            first = normalize(form).split()
            if not first:
                continue
            for label in self.token_labels.get(first[0], ()):
                start = phrase_position(label, form)
                if start is None or (len(first) == 1 and surface(label) != surface(form)):
                    continue
                if {"không", "chưa", "thiếu", "without", "no", "not"}.intersection(
                    label.split()[max(0, start - 3):start]
                ):
                    continue
                result.update((self.tag_labels if tags_only else self.labels).get(label, ()))
        return frozenset(result)

    def conjunction(self, term: str) -> list[str]:
        text = surface(term)
        if {"không", "trừ", "hoặc", "without", "not", "or"}.intersection(text.split()):
            return []
        parts = re.split(r"\s+(?:và|lẫn|and)\s+", text)
        if not 2 <= len(parts) <= 4:
            return []
        if parts[0].startswith("cả "):
            parts[0] = parts[0][3:]
        # Only explicit, wholly grounded clauses receive an annotation conjunction.
        if any(not self.positions(part) for part in parts):
            return []
        return list(dict.fromkeys(parts))
