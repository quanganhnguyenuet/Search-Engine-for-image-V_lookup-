"""/api/v1/admin: every route here needs a signed-in user whose token has user_role = admin."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from visualseek_api.api.routes.search import loaded_service
from visualseek_api.application.admin import AdminService, _actor
from visualseek_api.application.imports import MAX_CSV_BYTES
from visualseek_api.core.auth import AuthUser, require_admin
from visualseek_api.core.errors import ApplicationError

router = APIRouter()
Admin = Annotated[AuthUser, Depends(require_admin)]
Filter = Literal[
    "all",
    "needs_review",
    "no_brand",
    "no_category",
    "no_images",
    "pending",
    "edited",
    "hidden_images",
    "uploaded",
    "imported",
]
Vocabulary = Literal["brand", "category", "purpose", "ingredient", "model", "place"]


class EntityChanges(BaseModel):
    # field -> new value; see visualseek_retrieval.admin_overlay.FIELDS
    changes: dict[str, Any] = Field(min_length=1, max_length=20)
    note: str = Field("", max_length=200)


class PreviewChanges(BaseModel):
    # Unsaved values of the edit form, on top of the saved drafts.
    changes: dict[str, Any] = Field(default_factory=dict, max_length=20)
    siblings: int = Field(3, ge=0, le=8)


class DiscardFields(BaseModel):
    fields: list[str] = Field(min_length=1, max_length=20)


class HideImage(BaseModel):
    hidden: bool
    reason: str = Field("", max_length=200)


class GroupDecision(BaseModel):
    """The entity of an import group: an existing ``entity_id``, or ``new``."""

    key: str = Field(min_length=1, max_length=300)
    entity_id: str | None = Field(None, max_length=120)
    new: bool = False


class RetryImport(BaseModel):
    key: str | None = Field(None, max_length=300)


def admin_service(request: Request) -> AdminService:
    service = getattr(request.app.state, "admin", None)
    if service is None:
        raise ApplicationError("Admin is not available.", code="admin_unavailable", status_code=503)
    return service


@router.get("/me", summary="The signed-in admin")
def me(user: Admin) -> dict:
    return {"id": user.id, "email": user.email, "role": user.role}


@router.get("/overview", summary="Counts for the admin home page")
def overview(request: Request, _: Admin) -> dict:
    return admin_service(request).overview(loaded_service(request))


@router.get("/entities", summary="Find entities")
def entities(
    request: Request,
    _: Admin,
    q: Annotated[str, Query(max_length=120)] = "",
    filter: Filter = "all",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    return admin_service(request).list_entities(loaded_service(request), q, filter, offset, limit)


@router.get("/entities/{entity_id}", summary="One entity with its relations, images and history")
def entity(request: Request, entity_id: str, _: Admin) -> dict:
    return admin_service(request).entity(loaded_service(request), entity_id)


@router.post("/entities/{entity_id}/changes", summary="Edit an entity (pending until a release)")
def edit(request: Request, entity_id: str, body: EntityChanges, user: Admin) -> dict:
    return admin_service(request).edit(
        loaded_service(request), entity_id, body.changes, body.note, user
    )


@router.post("/entities/{entity_id}/preview", summary="Relations as the next release builds them")
def preview(request: Request, entity_id: str, body: PreviewChanges, _: Admin) -> dict:
    return admin_service(request).preview(
        loaded_service(request), entity_id, body.changes, body.siblings
    )


@router.post("/entities/{entity_id}/discard", summary="Drop pending edits of an entity")
def discard(request: Request, entity_id: str, body: DiscardFields, user: Admin) -> dict:
    return admin_service(request).discard(loaded_service(request), entity_id, body.fields, user)


@router.get("/vocab/{kind}", summary="Brands, categories and relation values to pick from")
def vocab(
    request: Request,
    kind: Vocabulary,
    _: Admin,
    q: Annotated[str, Query(max_length=80)] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[dict]:
    return admin_service(request).vocab(loaded_service(request), kind, q, limit)


@router.post("/images/{asset_id}/visibility", summary="Hide an image from search, or show it")
def visibility(request: Request, asset_id: str, body: HideImage, user: Admin) -> dict:
    return admin_service(request).set_hidden(
        loaded_service(request), asset_id, body.hidden, body.reason, user
    )


@router.post("/entities/{entity_id}/images", summary="Upload an image of an entity")
async def upload(
    request: Request,
    entity_id: str,
    user: Admin,
    file: Annotated[UploadFile, File()],
    caption: Annotated[str, Form(max_length=300)] = "",
) -> dict:
    admin = admin_service(request)
    # Read one byte past the limit, so an oversized file is refused without reading all of it.
    data = await file.read(admin.settings.admin_upload_max_bytes + 1)
    return await run_in_threadpool(
        admin.upload, loaded_service(request), entity_id, data, caption, user
    )


@router.delete("/uploads/{asset_id}", summary="Delete an image uploaded in admin")
def delete_upload(request: Request, asset_id: str, user: Admin) -> dict:
    return admin_service(request).delete_upload(loaded_service(request), asset_id, user)


@router.get("/release", summary="Pending edits, the running release and past releases")
def release_status(request: Request, _: Admin) -> dict:
    return admin_service(request).release_status(loaded_service(request))


@router.post("/release", summary="Apply the pending edits to the search")
def release(request: Request, user: Admin) -> dict:
    return admin_service(request).start_release(loaded_service(request), user)


@router.post("/imports", summary="Import approved images from a CSV (runs in the background)")
async def create_import(request: Request, user: Admin, file: Annotated[UploadFile, File()]) -> dict:
    admin = admin_service(request)
    loaded_service(request)  # refuse before reading when the search is not loaded
    data = await file.read(MAX_CSV_BYTES + 1)
    return await run_in_threadpool(
        admin.imports.create, file.filename or "import.csv", data, _actor(user)
    )


@router.get("/imports", summary="CSV imports, newest first")
def list_imports(request: Request, _: Admin) -> list[dict]:
    return admin_service(request).imports.list()


@router.get("/imports/{import_id}", summary="One import: its groups, new entities and issues")
def get_import(request: Request, import_id: str, _: Admin) -> dict:
    return admin_service(request).imports.view(import_id)


@router.post("/imports/{import_id}/decide", summary="Match an import group to an entity, or new")
def decide_import(request: Request, import_id: str, body: GroupDecision, user: Admin) -> dict:
    loaded_service(request)
    return admin_service(request).imports.decide(
        import_id, body.key, body.entity_id, body.new, _actor(user)
    )


@router.post("/imports/{import_id}/retry", summary="Run the failed steps of an import again")
def retry_import(request: Request, import_id: str, body: RetryImport, _: Admin) -> dict:
    return admin_service(request).imports.retry(import_id, body.key)
