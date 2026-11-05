from functools import lru_cache

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

    embedding_artifact_dir: str = "data/embeddings/culture-siglip2"
    embedding_cache_dir: str = "data/models/huggingface"
    embedding_device: str = "cpu"
    embedding_cpu_threads: int = 4
    ollama_url: str = "http://localhost:11434"
    rag_model: str = "qwen2.5:3b"
    rag_min_score: float = 0.10
    rag_timeout_seconds: float = 120

    r2_endpoint_url: str = "http://localhost:9000"
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "visualseek-images"
    r2_region: str = "auto"


@lru_cache
def get_settings() -> Settings:
    return Settings()
