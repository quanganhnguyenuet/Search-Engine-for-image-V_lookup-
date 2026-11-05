"""Local phrase ranking for a single named subject, including close title/tag variants."""

from dataclasses import dataclass
from functools import lru_cache

from visualseek_retrieval.text import normalize, surface


def phrase_position(text: str, query: str) -> int | None:
    """Match whole words; preserve explicit accents while accepting unaccented source labels."""
    words, requested = surface(text).split(), surface(query).split()
    if not requested:
        return None
    folded = [normalize(word) for word in requested]
    for start in range(len(words) - len(requested) + 1):
        window = words[start:start + len(requested)]
        if [normalize(word) for word in window] != folded:
            continue
        # 'phở' must not become 'phố'; ASCII labels such as 'pho' remain usable.
        if any(q != w and normalize(q) != q and normalize(w) != w
               for q, w in zip(requested, window, strict=True)):
            continue
        return start
    return None


@dataclass(frozen=True, slots=True)
class EntityMatch:
    position: int
    score: float
    kind: str
    exact_title: bool
    title_prefix: bool
    title_phrase: bool
    exact_tag: bool
    tag_prefix: bool
    extra_words: int
    description_phrase: bool
    reason: str


class EntityIndex:
    def __init__(self, assets: list[dict]):
        self.assets = assets
        self.titles = [normalize(str(row.get("keyword", ""))) for row in assets]
        self.title_postings: dict[str, list[int]] = {}
        self.tag_postings: dict[str, list[int]] = {}
        for position, row in enumerate(assets):
            if self.titles[position]:
                self.title_postings.setdefault(self.titles[position], []).append(position)
            for tag in {normalize(str(value)) for value in row.get("tags", [])} - {""}:
                self.tag_postings.setdefault(tag, []).append(position)
        # Index distinct label phrases, not every asset/description n-gram.
        self.title_phrases: dict[str, set[str]] = {}
        for title in self.title_postings:
            words = title.split()
            for start in range(len(words)):
                for end in range(start + 1, len(words) + 1):
                    self.title_phrases.setdefault(" ".join(words[start:end]), set()).add(title)
        self.tag_prefixes: dict[str, set[str]] = {}
        for tag in self.tag_postings:
            words = tag.split()
            for end in range(1, len(words) + 1):
                self.tag_prefixes.setdefault(" ".join(words[:end]), set()).add(tag)
        # Per-instance cache avoids retaining old indexes through a global method cache.
        self.ranked_matches = lru_cache(maxsize=128)(self._ranked_matches)

    def search(self, term: str, *, category: str | None, limit: int,
               include_variants: bool = True) -> list[dict] | None:
        matches = self.ranked_matches(surface(term), include_variants)
        if not matches:
            return None
        results = []
        for hit in matches:
            row = self.assets[hit.position]
            if category and row["category"] != category:
                continue
            results.append({"asset": row, "score": hit.score, "match_kind": hit.kind,
                            "match_reason": hit.reason,
                            "scores": {"exact_title": hit.exact_title,
                                       "title_prefix": hit.title_prefix,
                                       "title_phrase": hit.title_phrase,
                                       "exact_tag": hit.exact_tag,
                                       "tag_prefix": hit.tag_prefix,
                                       "variant_extra_words": hit.extra_words,
                                       "description_phrase": hit.description_phrase}})
            if len(results) == limit:
                break
        return results

    def _ranked_matches(self, query: str, include_variants: bool) -> tuple[EntityMatch, ...]:
        key = normalize(query)
        if not key:
            return ()
        title_labels = (self.title_phrases.get(key, set()) if include_variants
                        else {key} if key in self.title_postings else set())
        tag_labels = (self.tag_prefixes.get(key, set()) if include_variants
                      else {key} if key in self.tag_postings else set())
        candidates = set()
        for title in title_labels:
            candidates.update(self.title_postings[title])
        for tag in tag_labels:
            candidates.update(self.tag_postings[tag])
        matches = []
        for i in candidates:
            row = self.assets[i]
            title = str(row.get("keyword", ""))
            title_position = phrase_position(title, query)
            title_extra = len(surface(title).split()) - len(query.split())
            exact_title = title_position == 0 and title_extra == 0
            title_prefix = title_position == 0 and title_extra > 0 and include_variants
            title_phrase = title_position is not None and include_variants
            matching_tags = [str(tag) for tag in row.get("tags", [])
                             if phrase_position(str(tag), query) == 0]
            exact_tag = any(normalize(tag) == key for tag in matching_tags)
            tag_prefix = bool(matching_tags) and include_variants
            if exact_title:
                tier, kind, extra = 5, "title_exact", 0
                reason = f"Khớp tên ảnh: {title}."
            elif title_prefix:
                tier, kind, extra = 4, "title_variant", title_extra
                reason = f"Khớp biến thể trong tên ảnh: {title}."
            elif title_phrase:
                tier, kind, extra = 3, "title_phrase", title_extra
                reason = f"Cụm từ xuất hiện trong tên ảnh: {title}."
            elif exact_tag:
                tier, kind, extra = 2, "tag_exact", 0
                reason = f"Khớp tag: {next(t for t in matching_tags if normalize(t) == key)}."
            elif tag_prefix:
                best = min(matching_tags, key=lambda tag: (len(surface(tag).split()), tag))
                tier, kind = 1, "tag_variant"
                extra = len(surface(best).split()) - len(query.split())
                reason = f"Khớp biến thể tag: {best}."
            else:
                continue
            desc_position = phrase_position(str(row.get("description", "")), query)
            # Tiers and shorter variants dominate; description support breaks close ties.
            evidence = (3 + 1 / (1 + desc_position)) if desc_position is not None else 0
            score = tier * 1000 - min(extra, 90) * 10 + evidence + 2 * exact_tag
            matches.append(EntityMatch(i, score, kind, exact_title, title_prefix, title_phrase,
                                       exact_tag, tag_prefix, extra, desc_position is not None,
                                       reason[:240]))
        matches.sort(key=lambda hit: (-hit.score, self.assets[hit.position]["asset_id"]))
        return tuple(matches)
