"""Qdrant collection schema, ranking and branch scores for search traces."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from qdrant_client import QdrantClient, models

from visualseek_retrieval.encoder import DIM
from visualseek_retrieval.lexical import SparseVector

COLLECTION = "products_v1_images"
DENSE = "doc"
SPARSE = "sparse"
IMAGE = "img"  # SigLIP2 image tower, see scripts/embed_images.py
IMAGE_DIM = 768
POINT_NAMESPACE = uuid.UUID("7b1d1f0e-3c55-4c1e-9a52-6a0f4f1d2b3c")
# What the ranker reads from a hit. Everything else shown with a result (image_path, page_title,
# domain, brand_ids) is in memory (asset_payloads), so it is not sent back for every candidate:
# a full payload is ~0.8 KB x 50-60 hits per search, which cost ~160 ms over the WAN.
RANK_PAYLOAD = models.PayloadSelectorInclude(
    include=["asset_id", "entity_ids", "categories", "market_tiers"]
)
PAYLOAD_INDEXES = (
    "entity_ids",
    "brand_ids",
    "domain",
    "categories",
    "category_l1s",
    "market_tiers",
)


def point_id(asset_id: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, asset_id))


KEEPALIVE_SECONDS = 120


def connect(
    url: str, api_key: str | None, timeout: int = 10, prefer_grpc: bool = False
) -> QdrantClient:
    """REST over a pooled httpx client, or gRPC when ``prefer_grpc`` (search serving only: the
    offline scripts keep REST).

    httpx drops an idle connection after 5 s, so a person who pauses between searches pays a new
    TCP + TLS handshake to a remote cluster on every one (measured: +~380 ms to Seoul, 712 ms ->
    263 ms per search once connections stay open). Keep them for KEEPALIVE_SECONDS; the server
    closes idle ones after roughly 30-40 s, which the API's heartbeat stays under. A local
    Qdrant keeps qdrant-client's own default (keep-alive off for localhost).
    """
    extra = {}
    if urlparse(url).hostname not in ("localhost", "127.0.0.1"):
        extra["limits"] = httpx.Limits(
            max_connections=8, max_keepalive_connections=4, keepalive_expiry=KEEPALIVE_SECONDS
        )
    return QdrantClient(
        url=url, api_key=api_key or None, timeout=timeout, prefer_grpc=prefer_grpc, **extra
    )


def ensure_collection(client: QdrantClient, name: str = COLLECTION) -> None:
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config={DENSE: models.VectorParams(size=DIM, distance=models.Distance.COSINE)},
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
    for key in PAYLOAD_INDEXES:
        client.create_payload_index(name, key, field_schema=models.PayloadSchemaType.KEYWORD)


def ensure_image_vector(client: QdrantClient, name: str = COLLECTION) -> bool:
    """Add the SigLIP2 ``img`` vector to an existing collection; True if it was created.

    Stored as float16: the source vectors are float16 already, so nothing is lost and the
    vector takes half the RAM of float32 on the free tier.
    """
    vectors = client.get_collection(name).config.params.vectors
    if isinstance(vectors, dict) and IMAGE in vectors:
        existing = vectors[IMAGE]
        if existing.size != IMAGE_DIM or existing.distance != models.Distance.COSINE:
            raise RuntimeError(f"{name}.{IMAGE} exists with an unexpected schema: {existing}")
        return False
    client.create_vector_name(
        name,
        IMAGE,
        models.DenseVectorNameConfig(
            dense=models.DenseVectorConfig(
                size=IMAGE_DIM, distance=models.Distance.COSINE, datatype=models.Datatype.FLOAT16
            )
        ),
    )
    return True


@dataclass
class EntityBoosts:
    """Entity sets from spotting (S1) and graph expansion (S2) that shape the score."""

    seeds: list[str] = field(default_factory=list)
    hops: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    w_seed: float = 1.0
    w_hop: float = 0.5
    w_anchor: float = 2.0
    # B2 category intent: a soft prior on payload ``categories`` / ``market_tiers``.
    categories: list[str] = field(default_factory=list)
    tiers: list[str] = field(default_factory=list)
    w_category: float = 0.0
    w_tier: float = 0.0
    category_prefetch: bool = False
    graph_prefetch: bool = True
    # A query of several categories ("lều, xe ô tô và xe máy"): one dense channel per group, so
    # every group has candidates although one query vector sits closest to only one of them.
    category_groups: list[str] = field(default_factory=list)

    def targets(self) -> list[str]:
        return sorted(set(self.seeds) | set(self.hops))

    def score(self, entity_ids: list[str]) -> float:
        entities = set(entity_ids)
        return (
            (self.w_seed if entities.intersection(self.seeds) else 0.0)
            + (self.w_hop if entities.intersection(self.hops) else 0.0)
            - (self.w_anchor if entities.intersection(self.anchors) else 0.0)
        )

    def category_score(self, categories: list[str], tiers: list[str]) -> float:
        return (self.w_category if set(categories) & set(self.categories) else 0.0) + (
            self.w_tier if set(tiers) & set(self.tiers) else 0.0
        )


def _in(entity_ids: list[str]) -> models.FieldCondition:
    return models.FieldCondition(key="entity_ids", match=models.MatchAny(any=entity_ids))


def _field_in(key: str, values: list[str]) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchAny(any=values))


def build_request(
    dense: list[float],
    sparse: SparseVector,
    boosts: EntityBoosts,
    *,
    depth: int = 100,
    filter_brand: str | None = None,
    blocked_assets: list[str] | None = None,
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
    if targets and boosts.graph_prefetch:
        # Guarantees candidates for spotted/expanded entities even when content search misses them.
        channels.append(
            models.Prefetch(
                query=dense, using=DENSE, filter=models.Filter(must=[_in(targets)]), limit=depth
            )
        )
    if boosts.categories and boosts.w_category and boosts.category_prefetch:
        # Candidates of the asked category even when the text channels rank them low.
        must = [_field_in("categories", boosts.categories)]
        if boosts.tiers:
            must.append(_field_in("market_tiers", boosts.tiers))
        channels.append(
            models.Prefetch(query=dense, using=DENSE, filter=models.Filter(must=must), limit=depth)
        )
    if len(boosts.category_groups) >= 2:
        for group in boosts.category_groups:
            must = [_field_in("categories", [group])]
            channels.append(
                models.Prefetch(
                    query=dense, using=DENSE, filter=models.Filter(must=must), limit=depth
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
    if boosts.categories and boosts.w_category:
        terms.append(
            models.MultExpression(
                mult=[boosts.w_category, _field_in("categories", boosts.categories)]
            )
        )
    if boosts.tiers and boosts.w_tier:
        terms.append(
            models.MultExpression(mult=[boosts.w_tier, _field_in("market_tiers", boosts.tiers)])
        )
    query = (
        models.FormulaQuery(formula=models.SumExpression(sum=terms))
        if len(terms) > 1
        else models.FusionQuery(fusion=models.Fusion.RRF)
    )
    query_filter = _filter(filter_brand, blocked_assets)
    if len(terms) == 1:
        # Without boosts the fusion itself is the final query; no extra nesting level.
        return {"prefetch": channels, "query": query, "query_filter": query_filter}
    return {"prefetch": [fused], "query": query, "query_filter": query_filter}


def _filter(brand_id: str | None, blocked_assets: list[str] | None) -> models.Filter | None:
    if not brand_id and not blocked_assets:
        return None
    return models.Filter(
        must=[models.FieldCondition(key="brand_ids", match=models.MatchValue(value=brand_id))]
        if brand_id
        else None,
        must_not=[models.HasIdCondition(has_id=[point_id(asset) for asset in blocked_assets])]
        if blocked_assets
        else None,
    )


def _channel_name(channel: models.Prefetch) -> str:
    if channel.using == SPARSE:
        return "sparse"
    conditions = channel.filter.must if channel.filter else []
    if any(getattr(c, "key", None) == "categories" for c in conditions):
        return "category_prefetch"
    return "graph_prefetch" if conditions else "dense"


def branch_scores(client: QdrantClient, collection: str, request: dict) -> dict:
    """Replay the ranker's prefetches as one batch to expose their raw scores and ranks."""
    channels = request["prefetch"]
    if isinstance(request["query"], models.FormulaQuery):
        channels = channels[0].prefetch
    queries = []
    for channel in channels:
        filters = [value for value in (channel.filter, request["query_filter"]) if value]
        queries.append(
            models.QueryRequest(
                query=channel.query,
                using=channel.using,
                filter=models.Filter(must=filters) if filters else None,
                limit=channel.limit,
                with_payload=False,
            )
        )
    names = [_channel_name(channel) for channel in channels]
    responses = client.query_batch_points(collection, requests=queries)
    return {
        name: {
            str(point.id): {"rank": rank, "score": point.score}
            for rank, point in enumerate(response.points, start=1)
        }
        for name, response in zip(names, responses, strict=True)
    }
