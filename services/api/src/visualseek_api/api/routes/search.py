import unicodedata
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StrictBool, model_validator

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


class TextSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    category: Category | None = None
    top_k: int = Field(default=24, ge=1, le=50)
    use_llm: StrictBool | None = None

    @model_validator(mode="after")
    def meaningful_query(self):
        self.query = unicodedata.normalize("NFC", self.query.strip())
        if not self.query:
            raise ValueError("Query must not be empty")
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
        "encoder_ready": service.encoder_ready,
        "query_model_last_success": service.query_model_ready,
        "readiness_note": "LLM status records last uncached plan, not a live availability probe",
        "indexed": len(index.assets),
        "searchable": index.searchable_count,
        "excluded_from_search": len(index.assets) - index.searchable_count,
        'label_revision': index.manifest.get('label_revision'),
        "export_complete": index.manifest.get("complete", False),
        "scope": index.manifest.get("scope", {}),
        "model": index.manifest["model_id"],
        "backend": "numpy-exact",
        "metadata_ready": index.metadata_vectors is not None,
        "contextual_backend": "numpy-hybrid-rrf",
        'query_routing': (service.settings.search_routing_mode
                          if service.settings.search_llm_enabled else 'local'),
        'routing_classifier_ready': service.intent_router is not None,
        'routing_classifier': service.intent_router.version if service.intent_router else None,
        'llm_enabled': service.settings.search_llm_enabled,
        "query_model": service.settings.search_llm_model,
        "reasoning_effort": (service.settings.openai_reasoning_effort
                             if service.settings.search_llm_model == "gpt-5.6-luna" else None),
        "llm_provider": "openai",
        "openai_key_configured": bool(service.settings.openai_api_key.get_secret_value().strip()),
        "artifact_fingerprint": index.manifest["files"],
        "search_config": {**service.fusion_config().as_dict(),
                          'query_roles': service.settings.search_query_roles,
                          'rerank_enabled': service.settings.search_llm_rerank,
                          'rerank_candidates': service.settings.search_rerank_candidates,
                          'rerank_evidence': service.settings.search_rerank_evidence},
        "pipeline_signature": service.search_signature(),
        "relevance_mode": service.settings.search_relevance_mode,
        "relevance_policy_configured": bool(service.settings.search_relevance_policy),
        **service.coverage(),
    }


class WarmupRequest(BaseModel):
    include_llm: bool = False


@router.post("/warmup")
def warmup(body: WarmupRequest, service: ServiceDependency):
    if body.include_llm:
        service.require_llm()
    if service.settings.search_llm_enabled and service.settings.search_routing_mode == 'auto':
        service.get_intent_router()
    index = service.get_index()
    if index.metadata_vectors is None:
        raise ApplicationError("Index chưa có metadata embeddings.",
                               code="metadata_index_unavailable", status_code=503)
    service.get_encoder().encode_documents(["ảnh văn hóa Việt Nam"])
    service.get_retriever().prepare()
    service.encoder_ready = True
    if body.include_llm:
        # Probe the model even if the same warm-up request was cached previously.
        try:
            service.understanding.plan("Tìm ảnh áo dài")
        except ApplicationError:
            service.query_model_ready = False
            raise
        service.query_model_ready = True
    return search_status(service)


@router.post("/semantic")
def semantic_search(body: SemanticRequest, service: ServiceDependency):
    return service.search(**body.model_dump())


@router.post("/text")
def text_search(body: TextSearchRequest, service: ServiceDependency):
    return service.text_search(**body.model_dump())


@router.post("/rag")
def rag_search(body: RagRequest, service: ServiceDependency):
    return service.rag(**body.model_dump())
