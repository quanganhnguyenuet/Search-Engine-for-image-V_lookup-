from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from visualseek_api.application.search import SearchService
from visualseek_api.core.config import get_settings
from visualseek_api.core.errors import ApplicationError

router = APIRouter(prefix="/search")
type Category = Literal[
    "am_thuc", "kien_truc", "phong_canh", "le_hoi", "trang_phuc", "thu_cong_my_nghe"
]


@lru_cache
def get_search_service() -> SearchService:
    return SearchService(get_settings())


ServiceDependency = Annotated[SearchService, Depends(get_search_service)]


class SemanticRequest(BaseModel):
    query: str | None = Field(default=None, min_length=1, max_length=1000)
    image_base64: str | None = Field(default=None, min_length=1, max_length=14_000_000)
    category: Category | None = None
    top_k: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def one_modality(self):
        if self.query is not None:
            self.query = self.query.strip()
        if bool(self.query) == bool(self.image_base64):
            raise ValueError("Provide exactly one nonempty query or image")
        return self


class RagRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    category: Category | None = None
    top_k: int = Field(default=5, ge=1, le=8)

    @model_validator(mode="after")
    def meaningful_query(self):
        self.query = self.query.strip()
        if not self.query:
            raise ValueError("Question must not be empty")
        return self


@router.get("/status")
def search_status(service: ServiceDependency):
    try:
        index = service.get_index()
    except ApplicationError:
        return {"index_ready": False, "model_loaded": False, "indexed": 0}
    return {
        "index_ready": True,
        "model_loaded": service.encoder is not None,
        "indexed": len(index.assets),
        "export_complete": index.manifest.get("complete", False),
        "scope": index.manifest.get("scope", {}),
        "model": index.manifest["model_id"],
        "backend": "numpy-exact",
    }


@router.post("/semantic")
def semantic_search(body: SemanticRequest, service: ServiceDependency):
    return service.search(**body.model_dump())


@router.post("/rag")
def rag_search(body: RagRequest, service: ServiceDependency):
    return service.rag(**body.model_dump())
