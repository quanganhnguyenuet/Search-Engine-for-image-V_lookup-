from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    database_url: str = "postgresql+asyncpg://visualseek:visualseek@localhost:5432/visualseek"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    embedding_artifact_dir: str = "data/embeddings/culture-contextual-demo-v1"
    embedding_cache_dir: str = "data/models/huggingface"
    embedding_device: str = "cpu"
    embedding_cpu_threads: int = 4
    corpus_manifest_path: str = "data/processed/culture-search/assets.jsonl"
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    search_llm_model: str = "gpt-4o-mini"
    search_llm_timeout_seconds: float = 90
    search_llm_enabled: bool = False
    search_routing_mode: Literal['auto', 'always'] = 'auto'
    search_llm_rerank: bool = False
    search_query_roles: bool = True
    search_image_weight: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    search_metadata_weight: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    search_lexical_weight: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    search_rrf_constant: int = Field(default=60, ge=1)
    search_candidate_depth: int = Field(default=100, ge=50, le=1000)
    search_rerank_candidates: int = Field(default=32, ge=1, le=100)
    search_rerank_scope: Literal['planned', 'all'] = 'planned'
    search_rerank_evidence: Literal['annotations', 'pixels'] = 'annotations'
    search_image_root: str = 'data/raw/vietnamese-culture-vqa'
    search_relevance_mode: Literal['off', 'shadow', 'enforce'] = 'shadow'
    search_relevance_policy: str | None = None
    rag_model: str = "gpt-4o-mini"
    rag_min_score: float = 0.10
    rag_timeout_seconds: float = 120

    r2_endpoint_url: str = "http://localhost:9000"
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "visualseek-images"
    r2_region: str = "auto"

    @model_validator(mode='after')
    def validate_search_configuration(self):
        if not any((self.search_image_weight, self.search_metadata_weight,
                    self.search_lexical_weight)):
            raise ValueError('At least one search channel weight must be positive')
        if self.search_relevance_mode == 'enforce' and not self.search_relevance_policy:
            raise ValueError('Enforced relevance requires a calibrated policy file')
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
