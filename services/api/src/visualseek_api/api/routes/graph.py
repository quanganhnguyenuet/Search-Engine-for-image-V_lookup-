from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from visualseek_api.application.graph_view import MAX_SEEDS
from visualseek_api.core.errors import ApplicationError
from visualseek_api.schemas.graph import GraphSubgraph

router = APIRouter()


@router.get("/graph/subgraph", response_model=GraphSubgraph, summary="Entity relations")
def subgraph(
    request: Request,
    ids: Annotated[list[str] | None, Query(alias="id", max_length=MAX_SEEDS)] = None,
    origins: Annotated[list[str] | None, Query(alias="origin", max_length=10)] = None,
    siblings: Annotated[int, Query(ge=0, le=8)] = 0,
    shared_only: bool = False,
) -> GraphSubgraph:
    """Nodes and edges around the given entities, from the snapshot the search runs on."""
    service = getattr(request.app.state, "search", None)
    if service is None:
        raise ApplicationError(
            "Search is not loaded; start the API with SEARCH_ENABLED=true.",
            code="search_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not ids and not origins:
        raise ApplicationError("Pass at least one id or origin.", code="invalid_graph_request")
    return GraphSubgraph(**service.graph_view.subgraph(ids or [], origins, siblings, shared_only))
