"""Reciprocal-rank fusion of pixels, annotation vectors and lexical matches."""

import numpy as np

from visualseek_retrieval.artifact import ArtifactIndex
from visualseek_retrieval.entities import EntityIndex
from visualseek_retrieval.text import normalize


class HybridRetriever:
    def __init__(self, index: ArtifactIndex):
        self.index = index
        self._fields = None
        self.entities = EntityIndex(index.assets)
        self.title_terms = self.entities.titles
        self.title_postings = self.entities.title_postings
        self.tag_postings = self.entities.tag_postings

    @property
    def fields(self):
        if self._fields is None:
            self._fields = [
                (self.title_terms[i], normalize(" ".join(row.get("tags", []))),
                 normalize(str(row.get("description", ""))))
                for i, row in enumerate(self.index.assets)
            ]
        return self._fields

    def exact_search(self, term: str, *, category: str | None, limit: int,
                     include_variants: bool = True) -> list[dict] | None:
        """Rank a single named subject and its phrase variants without online inference."""
        return self.entities.search(term, category=category, limit=limit,
                                    include_variants=include_variants)

    def search(self, vectors: np.ndarray, terms: list[str], *, category: str | None,
               limit: int) -> list[dict]:
        index = self.index
        if index.metadata_vectors is None:
            raise ValueError("Metadata embeddings required; enrich or re-export the artifact")
        selected = np.array([
            i for i, row in enumerate(index.assets) if not category or row["category"] == category
        ], dtype=np.int64)
        if not len(selected):
            return []
        queries = np.asarray(vectors, dtype=np.float32)
        if (queries.ndim != 2 or queries.shape[1] != index.dimension
            or not np.isfinite(queries).all()
            or np.any(np.linalg.norm(queries, axis=1) <= 1e-12)):
            raise ValueError("Invalid hybrid query vectors")
        queries = queries / np.linalg.norm(queries, axis=1, keepdims=True)
        image = np.max(index.vectors[selected] @ queries.T, axis=1)
        metadata = np.max(index.metadata_vectors[selected] @ queries.T, axis=1)
        tokens = set(normalize(" ".join(terms)).split())
        lexical = np.asarray([
            sum(weight * len(tokens.intersection(field.split()))
                for weight, field in zip((3, 2, 1), self.fields[i], strict=True))
            for i in selected
        ], dtype=np.float32)
        # Fuse ranks, not incompatible cosine and keyword score scales.
        fused = np.zeros(len(selected), dtype=np.float64)
        depth = min(100, len(selected))
        for scores, positive_only in ((image, False), (metadata, False), (lexical, True)):
            order = np.argsort(-scores, kind="stable")[:depth]
            for rank, position in enumerate(order, 1):
                if not positive_only or scores[position] > 0:
                    fused[position] += 1 / (60 + rank)
        order = np.argsort(-fused, kind="stable")[:limit]
        return [{"asset": index.assets[int(selected[i])], "score": float(fused[i]),
                 "scores": {"image_cosine": float(image[i]),
                            "metadata_cosine": float(metadata[i]),
                            "lexical": float(lexical[i])}} for i in order]
