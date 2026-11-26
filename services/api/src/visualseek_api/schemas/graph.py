from typing import Literal

from pydantic import BaseModel

NodeType = Literal["entity", "brand", "category", "purpose", "ingredient", "model", "place"]


class GraphNode(BaseModel):
    id: str
    label: str
    type: NodeType
    # origin: the entity the query started from; seed: a result entity; sibling: a neighbour
    # added for context; value: a brand, category, purpose... shared by entities.
    role: Literal["origin", "seed", "sibling", "value"]
    degree: int
    props: dict[str, str | int]


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    rel: str
    origin: str | None = None
    confidence: float | None = None
    reviewed: bool | None = None


class GraphSubgraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool
