"""Corpus images. Public: <img> tags cannot send a token, and the sign-in page shows examples."""

import re

from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse

from visualseek_api.api.routes.search import loaded_service
from visualseek_api.core.errors import ApplicationError

router = APIRouter()
ASSET_ID = re.compile(r"^[0-9a-f]{64}$")


@router.get("/images/{asset_id}", summary="Serve a corpus image")
async def image(asset_id: str, request: Request) -> FileResponse:
    if not ASSET_ID.match(asset_id):
        raise ApplicationError("Invalid asset id.", code="invalid_asset_id")
    path = loaded_service(request).image_file(asset_id)
    admin = getattr(request.app.state, "admin", None)
    if path is None and admin is not None:
        # An image of a CSV import that is not in the collection yet (admin thumbnails).
        path = admin.imports.staged_file(asset_id)
    if path is None:
        raise ApplicationError(
            "Image not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND
        )
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})
