from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class SearchQuery:
    vector: tuple[float, ...]
    limit: int = 20
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchResult:
    asset_id: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorRetriever(Protocol):
    """Searches an external vector index such as Qdrant."""

    async def search(self, query: SearchQuery) -> list[SearchResult]: ...

