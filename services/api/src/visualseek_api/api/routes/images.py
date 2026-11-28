"""Corpus images. Public: <img> tags cannot send a token, and the sign-in page shows examples."""

import re
from pathlib import Path

from fastapi import APIRouter, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from visualseek_api.api.routes.search import loaded_service
from visualseek_api.application.thumbnails import THUMB_EDGE, thumbnail
from visualseek_api.core.config import get_settings
from visualseek_api.core.errors import ApplicationError

router = APIRouter()
ASSET_ID = re.compile(r"^[0-9a-f]{64}$")


@router.get("/images/{asset_id}", summary="Serve a corpus image")
async def image(asset_id: str, request: Request, w: int | None = None) -> FileResponse:
    """The original image, or with ``?w=480`` its small WebP copy for grids."""
    if not ASSET_ID.match(asset_id):
        raise ApplicationError("Invalid asset id.", code="invalid_asset_id")
    if w is not None and w != THUMB_EDGE:
        # One size only: the route is public, and every size would fill the disk cache.
        raise ApplicationError(f"The only thumbnail width is {THUMB_EDGE}.", code="invalid_width")
    path = loaded_service(request).image_file(asset_id)
    admin = getattr(request.app.state, "admin", None)
    if path is None and admin is not None:
        # An image of a CSV import that is not in the collection yet (admin thumbnails).
        path = admin.imports.staged_file(asset_id)
    if path is None:
        raise ApplicationError(
            "Image not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND
        )
    media_type = None
    if w is not None:
        cache_dir = Path(get_settings().thumbs_dir)
        small = await run_in_threadpool(thumbnail, path, asset_id, cache_dir)
        if small != path:
            path, media_type = small, "image/webp"
    return FileResponse(
        path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"}
    )
