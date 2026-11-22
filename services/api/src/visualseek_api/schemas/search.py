from typing import Any

from pydantic import BaseModel, Field


class TextSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    brand_id: str | None = Field(default=None, max_length=120)


class SearchHit(BaseModel):
    asset_id: str
    score: float
    entity_ids: list[str]
    brand_ids: list[str] | None = None
    image_path: str | None = None
    page_title: str | None = None
    domain: str | None = None
    entity_names: list[str] = []
    image_url: str


class TextSearchResponse(BaseModel):
    query: str
    results: list[SearchHit]
    out_of_scope: bool
    clarification: list[dict[str, Any]] | None
    plan: dict[str, Any]
    timings: dict[str, float]
    cache: dict[str, bool]
    graph_fingerprint: str
