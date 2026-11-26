import asyncio
import re

from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse

from visualseek_api.application.search import SearchService
from visualseek_api.core.errors import ApplicationError
from visualseek_api.schemas.search import TextSearchRequest, TextSearchResponse

router = APIRouter()
ASSET_ID = re.compile(r"^[0-9a-f]{64}$")


def _service(request: Request) -> SearchService:
    service = getattr(request.app.state, "search", None)
    if service is None:
        raise ApplicationError(
            "Search is not loaded; start the API with SEARCH_ENABLED=true.",
            code="search_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return service


@router.post("/search/text", response_model=TextSearchResponse, summary="Text to image search")
async def search_text(body: TextSearchRequest, request: Request) -> TextSearchResponse:
    service = _service(request)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        service.executor, service.search, body.query.strip(), body.brand_id
    )
    names = {e["id"]: e["name"] for e in service.pipeline.graph["entities"]}
    for hit in result["results"]:
        hit["entity_names"] = [names.get(e, e) for e in hit.get("entity_ids") or []]
        hit["image_url"] = f"/api/v1/images/{hit['asset_id']}"
        hit.update(service.details.get(hit["asset_id"], {}))
    return TextSearchResponse(**result)


@router.get("/search/info", summary="Loaded index, graph and settings")
async def search_info(request: Request) -> dict:
    return _service(request).info


@router.get("/images/{asset_id}", summary="Serve a corpus image")
async def image(asset_id: str, request: Request) -> FileResponse:
    if not ASSET_ID.match(asset_id):
        raise ApplicationError("Invalid asset id.", code="invalid_asset_id")
    path = _service(request).image_file(asset_id)
    if path is None:
        raise ApplicationError(
            "Image not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND
        )
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})
