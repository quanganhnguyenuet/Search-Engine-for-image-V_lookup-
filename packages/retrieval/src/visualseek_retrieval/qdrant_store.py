"""Qdrant collection schema and the single ``query_points`` request used per search."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from qdrant_client import QdrantClient, models

from visualseek_retrieval.encoder import DIM
from visualseek_retrieval.lexical import SparseVector

COLLECTION = "products_v1_images"
DENSE = "doc"
SPARSE = "sparse"
POINT_NAMESPACE = uuid.UUID("7b1d1f0e-3c55-4c1e-9a52-6a0f4f1d2b3c")
PAYLOAD_INDEXES = ("entity_ids", "brand_ids", "domain")


def point_id(asset_id: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, asset_id))


def connect(url: str, api_key: str | None, timeout: int = 10) -> QdrantClient:
    # REST over a pooled httpx client keeps the TLS session alive between requests.
    return QdrantClient(url=url, api_key=api_key or None, timeout=timeout, prefer_grpc=False)


def ensure_collection(client: QdrantClient, name: str = COLLECTION) -> None:
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config={DENSE: models.VectorParams(size=DIM, distance=models.Distance.COSINE)},
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
    for key in PAYLOAD_INDEXES:
        client.create_payload_index(name, key, field_schema=models.PayloadSchemaType.KEYWORD)


@dataclass
class EntityBoosts:
    """Entity sets from spotting (S1) and graph expansion (S2) that shape the score."""

    seeds: list[str] = field(default_factory=list)
    hops: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    w_seed: float = 1.0
    w_hop: float = 0.5
    w_anchor: float = 2.0

    def targets(self) -> list[str]:
        return sorted(set(self.seeds) | set(self.hops))


def _in(entity_ids: list[str]) -> models.FieldCondition:
    return models.FieldCondition(key="entity_ids", match=models.MatchAny(any=entity_ids))


def build_request(
    dense: list[float],
    sparse: SparseVector,
    boosts: EntityBoosts,
    *,
    depth: int = 100,
    filter_brand: str | None = None,
) -> dict:
    """Arguments for ``query_points``/``query_points_groups``: one round-trip per query."""
    channels = [
        models.Prefetch(query=dense, using=DENSE, limit=depth),
        models.Prefetch(
            query=models.SparseVector(indices=sparse.indices, values=sparse.values),
            using=SPARSE,
            limit=depth,
        ),
    ]
    targets = boosts.targets()
    if targets:
        # Guarantees candidates for spotted/expanded entities even when content search misses them.
        channels.append(
            models.Prefetch(
                query=dense, using=DENSE, filter=models.Filter(must=[_in(targets)]), limit=depth
            )
        )
    fused = models.Prefetch(
        prefetch=channels, query=models.FusionQuery(fusion=models.Fusion.RRF), limit=depth * 2
    )
    terms: list = ["$score"]
    if boosts.seeds:
        terms.append(models.MultExpression(mult=[boosts.w_seed, _in(boosts.seeds)]))
    if boosts.hops:
        terms.append(models.MultExpression(mult=[boosts.w_hop, _in(boosts.hops)]))
    if boosts.anchors:
        terms.append(models.MultExpression(mult=[-boosts.w_anchor, _in(boosts.anchors)]))
    query = (
        models.FormulaQuery(formula=models.SumExpression(sum=terms))
        if len(terms) > 1
        else models.FusionQuery(fusion=models.Fusion.RRF)
    )
    if len(terms) == 1:
        # Without boosts the fusion itself is the final query; no extra nesting level.
        return {"prefetch": channels, "query": query, "query_filter": _brand(filter_brand)}
    return {"prefetch": [fused], "query": query, "query_filter": _brand(filter_brand)}


def _brand(brand_id: str | None) -> models.Filter | None:
    if not brand_id:
        return None
    return models.Filter(
        must=[models.FieldCondition(key="brand_ids", match=models.MatchValue(value=brand_id))]
    )
