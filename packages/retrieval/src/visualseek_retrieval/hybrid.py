"""Independent retrieval channels, indexed lexical scoring and weighted rank fusion."""

import threading
from collections import defaultdict
from dataclasses import asdict, dataclass

import numpy as np

from visualseek_retrieval.artifact import ArtifactIndex
from visualseek_retrieval.entities import EntityIndex
from visualseek_retrieval.text import accent_key, normalize, surface


@dataclass(frozen=True)
class FusionConfig:
    image_weight: float = 1.0
    metadata_weight: float = 1.0
    lexical_weight: float = 1.0
    rank_constant: int = 60
    image_depth: int = 100
    metadata_depth: int = 100
    lexical_depth: int = 100

    def __post_init__(self):
        weights = (self.image_weight, self.metadata_weight, self.lexical_weight)
        if not all(np.isfinite(w) and w >= 0 for w in weights) or not any(weights):
            raise ValueError("Fusion weights must be finite, nonnegative and not all zero")
        if (
            self.rank_constant < 1
            or min(self.image_depth, self.metadata_depth, self.lexical_depth) < 1
        ):
            raise ValueError("Rank constant and candidate depths must be positive")

    def as_dict(self) -> dict:
        return asdict(self)


class HybridRetriever:
    def __init__(self, index: ArtifactIndex, config: FusionConfig | None = None):
        self.index = index
        self.config = config or FusionConfig()
        self._fields = None
        self._lexical_postings = None
        self._accent_postings = None
        self._legacy_postings = None
        self._prepare_lock = threading.Lock()
        self.entities = EntityIndex(index.assets)
        self.title_terms = self.entities.titles
        self.title_postings = self.entities.title_postings
        self.tag_postings = self.entities.tag_postings
        self.positions = {
            None: np.asarray(
                [i for i, row in enumerate(index.assets) if not row.get("search_excluded")],
                dtype=np.int64,
            )
        }
        for category in index.counts:
            self.positions[category] = np.asarray(
                [
                    i
                    for i, row in enumerate(index.assets)
                    if row["category"] == category and not row.get("search_excluded")
                ],
                dtype=np.int64,
            )

    @property
    def fields(self):
        if self._fields is None:
            self._fields = [
                (
                    self.title_terms[i],
                    normalize(" ".join(row.get("tags", []))),
                    normalize(str(row.get("description", ""))),
                )
                for i, row in enumerate(self.index.assets)
            ]
        return self._fields

    def prepare(self) -> None:
        """Build postings once; exact entity lookups never scan descriptions."""
        if self._lexical_postings is not None:
            return
        with self._prepare_lock:
            if self._lexical_postings is not None:
                return
            postings = defaultdict(dict)
            accented, legacy = defaultdict(dict), defaultdict(dict)
            for position, row in enumerate(self.index.assets):
                if row.get("search_excluded"):
                    continue
                fields = (
                    str(row.get("keyword", "")),
                    " ".join(row.get("tags", [])),
                    str(row.get("description", "")),
                )
                for bit, field in zip((1, 2, 4), fields, strict=True):
                    text = surface(field)
                    is_ascii = text == normalize(text)
                    for token in set(text.split()):
                        folded = normalize(token)
                        key = accent_key(token)
                        postings[folded][position] = postings[folded].get(position, 0) | bit
                        accented[key][position] = accented[key].get(position, 0) | bit
                        if is_ascii:
                            legacy[folded][position] = legacy[folded].get(position, 0) | bit

            def pack(source):
                return {
                    token: (
                        np.fromiter(values, dtype=np.int32),
                        np.fromiter(values.values(), dtype=np.uint8),
                    )
                    for token, values in source.items()
                }

            self._accent_postings, self._legacy_postings = pack(accented), pack(legacy)
            self._lexical_postings = pack(postings)

    def lexical_scores(self, terms: list[str]) -> np.ndarray:
        self.prepare()
        scores = np.zeros(len(self.index.assets), dtype=np.float32)
        requested = surface(" ".join(terms))
        strict = requested != normalize(requested)
        weights = np.asarray([0, 3, 2, 5, 1, 4, 3, 6], dtype=np.float32)
        for token in set(requested.split()):
            if not strict:
                posting = self._lexical_postings.get(normalize(token))
                if posting is not None:
                    positions, masks = posting
                    scores[positions] += weights[masks]
            else:
                masks = np.zeros(len(scores), dtype=np.uint8)
                for posting in (
                    self._accent_postings.get(accent_key(token)),
                    self._legacy_postings.get(normalize(token)),
                ):
                    if posting is not None:
                        positions, bits = posting
                        masks[positions] |= bits
                scores += weights[masks]
        return scores

    def exact_search(
        self, term: str, *, category: str | None, limit: int, include_variants: bool = True
    ) -> list[dict] | None:
        return self.entities.search(
            term, category=category, limit=limit, include_variants=include_variants
        )

    def _cosine(self, matrix: np.ndarray, queries: np.ndarray) -> np.ndarray:
        values = np.asarray(queries, dtype=np.float32)
        if (
            values.ndim != 2
            or not len(values)
            or values.shape[1] != self.index.dimension
            or not np.isfinite(values).all()
        ):
            raise ValueError("Invalid hybrid query vectors")
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        if np.any(norms <= 1e-12):
            raise ValueError("Invalid hybrid query vectors")
        # Multiply the memory-mapped matrix directly; do not copy two full matrices
        # for every unfiltered request through advanced NumPy indexing.
        return np.max(matrix @ (values / norms).T, axis=1)

    def channel_scores(
        self,
        vectors: np.ndarray,
        terms: list[str],
        *,
        image_vectors: np.ndarray | None = None,
        metadata_vectors: np.ndarray | None = None,
    ) -> dict[str, np.ndarray]:
        if self.index.metadata_vectors is None:
            raise ValueError("Metadata embeddings required; enrich or re-export the artifact")
        return {
            "image": self._cosine(
                self.index.vectors, vectors if image_vectors is None else image_vectors
            ),
            "metadata": self._cosine(
                self.index.metadata_vectors,
                vectors if metadata_vectors is None else metadata_vectors,
            ),
            "lexical": self.lexical_scores(terms),
        }

    def rank(
        self,
        scores: dict[str, np.ndarray],
        *,
        category: str | None,
        limit: int,
        config: FusionConfig | None = None,
        exclusions: list[str] | None = None,
    ) -> list[dict]:
        if limit < 1:
            raise ValueError("limit must be positive")
        selected = self.positions.get(category, np.empty(0, dtype=np.int64))
        if exclusions:
            excluded = self.entities.excluded_positions(tuple(sorted(set(exclusions))))
            selected = selected[np.asarray([int(i) not in excluded for i in selected], dtype=bool)]
        if not len(selected):
            return []
        config = config or self.config
        fused = np.zeros(len(selected), dtype=np.float64)
        channel_ranks = {}
        for channel in ("image", "metadata", "lexical"):
            values = scores[channel][selected]
            weight = getattr(config, channel + "_weight")
            ranks = np.zeros(len(selected), dtype=np.int32)
            if weight:
                order = np.argsort(-values, kind="stable")[: getattr(config, channel + "_depth")]
                if channel == "lexical":
                    order = order[values[order] > 0]
                ranks[order] = np.arange(1, len(order) + 1)
                fused[order] += weight / (config.rank_constant + ranks[order])
            channel_ranks[channel] = ranks
        candidates = np.flatnonzero(fused > 0)
        order = candidates[np.argsort(-fused[candidates], kind="stable")[:limit]]
        hits = []
        for i in order:
            position = int(selected[i])
            row = self.index.assets[position]
            hits.append(
                {
                    "asset": row,
                    "score": float(fused[i]),
                    "scores": {
                        "image_cosine": float(scores["image"][position]),
                        "metadata_cosine": float(scores["metadata"][position]),
                        "lexical": float(scores["lexical"][position]),
                    },
                    "ranks": {key: int(value[i]) or None for key, value in channel_ranks.items()},
                    "metadata_complete": bool(row.get("description", "").strip()),
                }
            )
        return hits

    def search(
        self,
        vectors: np.ndarray,
        terms: list[str],
        *,
        category: str | None,
        limit: int,
        image_vectors: np.ndarray | None = None,
        metadata_vectors: np.ndarray | None = None,
        exclusions: list[str] | None = None,
    ) -> list[dict]:
        if category is not None and category not in self.positions:
            return []
        scores = self.channel_scores(
            vectors, terms, image_vectors=image_vectors, metadata_vectors=metadata_vectors
        )
        return self.rank(scores, category=category, limit=limit, exclusions=exclusions)
