"""Category intent of a query (B2): which taxonomy category and market tier it asks for.

The lexicon is built offline-free from graph/entity_categories.json: every word n-gram of an
entity's ``product_type`` votes for that entity's category, and an n-gram becomes a cue only
when one category owns most of its votes ("ao dai" -> culture.costume, but "xe" or "may" are
spread over many categories and are dropped). No model and no LLM at query time.

A category cue only re-ranks (a small score boost on payload ``categories``) and adds a
category-filtered dense channel so the right objects are candidates; it never filters hard,
because labels are about 98 % accurate. Tier cues apply to fashion only.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from visualseek_retrieval.text import normalize, surface

MAX_NGRAM = 4
MIN_SUPPORT = 3  # 2 let "máy lọc nước" (two pet fountains) mean pet supplies
MIN_SHARE = 0.75
# A single syllable is ambiguous in Vietnamese ("ao", "anh", "may"): it must be nearly pure.
MIN_SHARE_ONE_WORD = 0.9
MIN_SUPPORT_ONE_WORD = 3
NAME_VOTES = 3  # a category's own Vietnamese name counts as this many entities
STOPWORDS = frozenset(
    (
        "va cua cho voi la co khong loai dang cac nhung mot the trong tren duoi tu den "
        "anh hinh tim kiem xem can cho muon"
    ).split()
)

# Cues that mean a tier whatever the object is, and cues that need a fashion object
# ("nhà cao cấp" or "máy lọc nước cao cấp" are not about fashion tiers).
TIER_CUES: tuple[tuple[str, re.Pattern, bool], ...] = tuple(
    (tier, re.compile(rf"\b({cues})\b"), alone)
    for tier, cues, alone in (
        ("luxury", "hang hieu|xa xi|hang sang|luxury|dat tien", True),
        ("luxury", "cao cap|sang trong|dang cap", False),
        ("premium", "tam trung|trung cap", False),
        ("mass", "binh dan|gia re|pho thong", False),
    )
)


def split_label(text: str) -> list[str]:
    return [part for part in re.split(r"[,&/;]", text) if normalize(part)]


def words_of(text: str, accented: bool) -> list[str]:
    return (surface(text) if accented else normalize(text)).split()


def ngrams(words: list[str], head: bool = False) -> set[tuple[str, ...]]:
    """Word n-grams; with ``head`` only those that start the phrase."""
    out = set()
    for n in range(1, MAX_NGRAM + 1):
        for start in range(min(1, len(words) - n + 1) if head else len(words) - n + 1):
            gram = tuple(words[start : start + n])
            if not all(normalize(w) in STOPWORDS or w.isdigit() for w in gram):
                out.add(gram)
    return out


@dataclass
class CategoryIntent:
    matches: list[dict] = field(default_factory=list)  # text, category, support, share
    categories: list[str] = field(default_factory=list)
    tiers: list[str] = field(default_factory=list)
    ignored_tiers: list[dict] = field(default_factory=list)  # tier, cue, reason

    def __bool__(self) -> bool:
        return bool(self.categories or self.tiers)


class CategoryLexicon:
    """Two tables over the same votes: accent-preserving (giày vs giấy) and folded."""

    def __init__(self, accented: dict, folded: dict, names: dict[str, str]):
        self.tables = {True: accented, False: folded}
        self.names = names  # category id -> Vietnamese name

    @staticmethod
    def _table(
        entities: dict[str, dict], names: dict[str, str], accented: bool, head: bool = False
    ) -> dict:
        votes: dict[tuple[str, ...], Counter] = defaultdict(Counter)
        for entity in entities.values():
            for gram in ngrams(words_of(entity["product_type"], accented), head):
                votes[gram][entity["category"]] += 1
        for category, name in names.items():
            for part in split_label(name):
                for gram in ngrams(words_of(part, accented), head):
                    votes[gram][category] += NAME_VOTES
        table = {}
        for gram, counter in votes.items():
            category, top = counter.most_common(1)[0]
            total = sum(counter.values())
            one = len(gram) == 1
            if total >= (MIN_SUPPORT_ONE_WORD if one else MIN_SUPPORT) and top / total >= (
                MIN_SHARE_ONE_WORD if one else MIN_SHARE
            ):
                table[gram] = (category, total, round(top / total, 3))
        return table

    @classmethod
    def build(cls, entities: dict[str, dict], names: dict[str, str]) -> CategoryLexicon:
        """Vietnamese noun phrases are head-first: "xe máy" in "Lốp xe máy" only qualifies a
        tyre. The votes of phrases at the head of a product type ("Xe máy điện") decide a cue
        first; votes from anywhere in the type are the fallback for the other phrases."""

        def table(accented: bool) -> dict:
            anywhere = cls._table(entities, names, accented)
            out = {gram: (*entry, False) for gram, entry in anywhere.items()}
            for gram, entry in cls._table(entities, names, accented, head=True).items():
                # Flag the cues that only the head votes make: in a query they must head their
                # phrase too ("xe máy" alone, not in "lốp xe máy").
                out[gram] = (*entry, anywhere.get(gram, (None,))[0] != entry[0])
            return out

        return cls(table(True), table(False), names)

    def detect(self, query: str) -> CategoryIntent:
        accented = surface(query) != normalize(query)
        words = words_of(query, accented)
        table = self.tables[accented]
        covered = [False] * len(words)
        matches = []
        for n in range(MAX_NGRAM, 0, -1):  # longest cue first
            for start in range(len(words) - n + 1):
                if any(covered[start : start + n]):
                    continue
                entry = table.get(tuple(words[start : start + n]))
                if entry and entry[3] and start and normalize(words[start - 1]) not in STOPWORDS:
                    continue  # a head-only cue qualifying the word before it
                if entry:
                    covered[start : start + n] = [True] * n
                    matches.append(
                        {
                            "start": start,
                            "text": " ".join(words[start : start + n]),
                            "category": entry[0],
                            "support": entry[1],
                            "share": entry[2],
                        }
                    )
        matches.sort(key=lambda m: m.pop("start"))
        categories = list(dict.fromkeys(m["category"] for m in matches))
        intent = CategoryIntent(matches=matches, categories=categories)
        fashion = any(c.startswith("fashion.") for c in categories)
        text = normalize(query)
        for tier, pattern, alone in TIER_CUES:
            found = pattern.search(text)
            if not found or tier in intent.tiers:
                continue
            if fashion or (alone and not categories):
                intent.tiers.append(tier)
            else:
                intent.ignored_tiers.append(
                    {
                        "tier": tier,
                        "cue": found.group(),
                        "reason": "not_fashion" if categories else "no_fashion_category",
                    }
                )
        return intent


def knn_votes(
    scores, entity_category: list[str | None], k: int = 10, floor: float = 0.3
) -> tuple[dict[str, float], float]:
    """Share of the vote per category among the k nearest entity cards, and the best cosine.

    ``scores`` are query-to-entity cosines, ``entity_category`` the category of every entity
    row. Each neighbour votes with (similarity - floor).
    """
    import numpy as np

    votes: dict[str, float] = defaultdict(float)
    order = np.argsort(-scores)[:k]
    for index in order:
        if entity_category[index]:
            votes[entity_category[index]] += max(float(scores[index]) - floor, 0.0)
    total = sum(votes.values())
    best = float(scores[order[0]]) if len(order) else 0.0
    return ({c: v / total for c, v in votes.items()} if total else {}), best
