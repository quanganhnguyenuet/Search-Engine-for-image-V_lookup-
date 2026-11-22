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

    # products-v1 text search; off by default so tests do not load the encoder.
    search_enabled: bool = False
    search_artifact_dir: str = "data/processed/products-v1/search"
    search_collection: str = "products_v1_images"
    search_embed_device: str = "cuda"
    products_image_root: str = ""
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
