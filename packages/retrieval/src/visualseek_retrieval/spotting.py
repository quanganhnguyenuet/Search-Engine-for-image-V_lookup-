"""S1 entity spotting: link query spans to catalogue entities/brands, no NER model.

Dictionary: every alias of graph.json (entity names, names without notes, unique model codes,
model tails, brand names) keyed by its accent-folded token sequence. Matching is greedy
longest-span, left to right, over the query's folded tokens (an Aho-Corasick-equivalent
token trie lookup). When both the query span and the alias carry Vietnamese marks, the
marks must agree, so "bánh chay" does not link to "bánh chày".
Dense: cosine between the query vector and one card vector per entity, used when the
dictionary finds nothing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from visualseek_retrieval.entity_review import EntityReview
from visualseek_retrieval.text import normalize, surface, token_matches

MAX_SPAN = 12


@dataclass(frozen=True)
class Entry:
    kind: str  # "entity" | "brand"
    id: str
    words: tuple[str, ...]  # alias surface words (accented when the source is)


@dataclass
class Match:
    kind: str
    ids: list[str]
    start: int
    end: int
    text: str


@dataclass
class Spotting:
    entities: list[str] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    ambiguous: list[Match] = field(default_factory=list)
    dense_top: list[tuple[str, float]] = field(default_factory=list)
    entity_scores: np.ndarray | None = None  # query-to-entity-card cosines, one per row


def _words(text: str) -> list[str]:
    return [word for word in surface(text).split() if normalize(word)]


query_words = _words  # the tokens that Match.start/end index


class EntitySpotter:
    def __init__(
        self,
        graph: dict,
        entity_dense: np.ndarray,
        review: EntityReview | None = None,
        short_aliases: dict[str, list[str]] | None = None,
        suppressed_aliases: dict[str, list[str]] | None = None,
    ) -> None:
        review = review or EntityReview()
        suppressed_aliases = suppressed_aliases or {}
        self.entity_ids = [review.canonical(entity["id"]) for entity in graph["entities"]]
        self.entity_dense = entity_dense
        self.table: dict[tuple[str, ...], list[Entry]] = defaultdict(list)
        for entity in graph["entities"]:
            # Prefer an accented spelling of the alias when the name provides one.
            spelled = {normalize(text): _words(text) for text in (entity["name"], entity["core"])}
            blocked = set(suppressed_aliases.get(entity["id"], ()))
            for alias in entity["aliases"]:
                if alias in blocked:
                    continue  # shared with other entities (scripts/build_short_aliases.py)
                words = spelled.get(alias) or alias.split()
                self._add(Entry("entity", review.canonical(entity["id"]), tuple(words)))
            for alias in review.aliases.get(entity["id"], ()):
                self._add(Entry("entity", review.canonical(entity["id"]), tuple(_words(alias))))
        # Collision-checked offline (scripts/build_short_aliases.py), keyed by canonical id.
        for entity_id, aliases in (short_aliases or {}).items():
            for alias in aliases:
                self._add(Entry("entity", review.canonical(entity_id), tuple(_words(alias))))
        for brand in graph["brands"]:
            for alias in {*brand["aliases"], normalize(brand["name"])}:
                words = (
                    _words(brand["name"]) if normalize(brand["name"]) == alias else alias.split()
                )
                self._add(Entry("brand", brand["id"], tuple(words)))

    def _add(self, entry: Entry) -> None:
        key = tuple(normalize(" ".join(entry.words)).split())
        if key and entry not in self.table[key]:
            self.table[key].append(entry)

    def _lookup(self, query_words: list[str]) -> list[Match]:
        folded = [normalize(word) for word in query_words]
        # A word can fold to several tokens only in odd cases; keep one folded form per word.
        folded = [token.split()[0] for token in folded]
        matches: list[Match] = []
        start = 0
        while start < len(folded):
            found = None
            for end in range(min(len(folded), start + MAX_SPAN), start, -1):
                entries = self.table.get(tuple(folded[start:end]))
                if not entries:
                    continue
                span = query_words[start:end]
                kept = [entry for entry in entries if _accents_agree(span, entry.words)]
                if kept:
                    found = (end, kept)
                    break
            if found is None:
                start += 1
                continue
            end, kept = found
            kinds = {entry.kind for entry in kept}
            kind = "entity" if "entity" in kinds else "brand"
            ids = sorted({entry.id for entry in kept if entry.kind == kind})
            matches.append(Match(kind, ids, start, end, " ".join(query_words[start:end])))
            start = end
        return matches

    def spot(self, query: str, query_vector: np.ndarray | None, top_k: int = 5) -> Spotting:
        result = Spotting()
        for match in self._lookup(_words(query)):
            if match.kind == "entity" and len(match.ids) > 1:
                result.ambiguous.append(match)
                continue
            result.matches.append(match)
            target = result.entities if match.kind == "entity" else result.brands
            for item in match.ids:
                if item not in target:
                    target.append(item)
        if query_vector is not None:
            scores = self.entity_dense @ query_vector
            result.entity_scores = scores
            unique = set()
            for index in np.argsort(-scores):
                entity_id = self.entity_ids[index]
                if len(result.dense_top) >= top_k:
                    break
                if entity_id not in unique:
                    result.dense_top.append((entity_id, float(scores[index])))
                    unique.add(entity_id)
        return result


def _accents_agree(query_words: list[str], alias_words: tuple[str, ...]) -> bool:
    if len(query_words) != len(alias_words):
        # Folding merged or split a token; fall back to the folded comparison already made.
        return True
    strict = any(word != normalize(word) for word in query_words) and any(
        word != normalize(word) for word in alias_words
    )
    return all(
        token_matches(alias, word, strict=strict)
        for alias, word in zip(alias_words, query_words, strict=True)
    )
