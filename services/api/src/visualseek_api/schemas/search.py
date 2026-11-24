from typing import Any, Literal

from pydantic import BaseModel, Field


class TextSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    brand_id: str | None = Field(default=None, max_length=120)


class RankedScore(BaseModel):
    rank: int
    score: float


class SearchScores(BaseModel):
    dense: RankedScore | None
    sparse: RankedScore | None
    graph_prefetch: RankedScore | None
    graph: float | None
    category: float | None = None
    img: float | None
    rrf: float


class TraceNode(BaseModel):
    id: str
    label: str


class TraceEvidence(BaseModel):
    field: str
    text: str


class TraceEdge(BaseModel):
    entity_id: str
    source_entity_id: str
    source: str | None
    confidence: float | None
    reviewed: bool
    evidence: list[TraceEvidence]


class TraceValue(TraceNode):
    kind: str
    origin_edges: list[TraceEdge]
    target_edges: list[TraceEdge]


class TraceCue(BaseModel):
    relation: str
    cue: str


class TraceNegation(BaseModel):
    cue: str
    applied: bool
    reason: Literal["no_targets", "no_origin", "not_searched"] | None


class TraceCategoryRef(BaseModel):
    id: str
    label: str


class TraceCategory(BaseModel):
    source: Literal["lexicon", "knn", "mixed"] | None
    categories: list[TraceCategoryRef]
    matches: list[dict[str, Any]]
    tiers: list[str]
    ignored_tiers: list[dict[str, str]]
    segments: list[dict[str, Any]] = []
    groups: list[str] = []


class TraceTypeRequest(BaseModel):
    brand: str
    include: list[str]
    exclude: list[str]
    named: list[str] = []
    of: int
    kept: int
    # absent: the type exists in the catalogue but the brand has none of it (no product kept);
    # category: no product of the type, but some of the brand's products share its category.
    status: Literal["none", "no_match", "no_narrowing", "filtered", "category", "absent"]


class SearchTrace(BaseModel):
    version: Literal[1]
    relation: str | None
    cue: str | None
    mode: Literal["shared_value", "value_filter"] | None
    origins: list[TraceNode]
    values: list[TraceValue]
    targets: list[TraceNode]
    excluded: list[TraceNode]
    brand_filter: TraceNode | None
    unsupported_cues: list[TraceCue]
    negation: TraceNegation | None
    category: TraceCategory | None = None
    type_requests: list[TraceTypeRequest] = []
    status: Literal[
        "clarification",
        "out_of_scope",
        "expanded",
        "no_target",
        "no_edge",
        "no_origin",
        "unsupported_relation",
        "category",
        "content",
    ]
    channels: dict[str, Literal["ready", "inactive", "unavailable"]]


class SearchHit(BaseModel):
    asset_id: str
    score: float
    scores: SearchScores
    entity_ids: list[str]
    brand_ids: list[str] | None = None
    image_path: str | None = None
    page_title: str | None = None
    domain: str | None = None
    categories: list[str] = []
    market_tiers: list[str] = []
    entity_names: list[str] = []
    image_url: str


class TextSearchResponse(BaseModel):
    query: str
    results: list[SearchHit]
    out_of_scope: bool
    clarification: list[dict[str, Any]] | None
    plan: dict[str, Any]
    trace: SearchTrace
    timings: dict[str, float]
    cache: dict[str, bool]
    graph_fingerprint: str
