from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    # Search and graph routes need a Supabase access token. Turn off only for local tools
    # that call the API without one, such as the evaluation scripts.
    require_login: bool = True
    supabase_url: str = ""

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    # gRPC (port 6334) to a remote cluster: binary vectors on one multiplexed connection. Measured
    # against Qdrant Cloud in Seoul: 791 ms -> 170 ms per search. Falls back to REST at start-up
    # if the port is unreachable; set QDRANT_PREFER_GRPC=false to force REST.
    qdrant_prefer_grpc: bool = True

    # products-v1 text search; off by default so tests do not load the encoder.
    search_enabled: bool = False
    search_artifact_dir: str = "data/processed/products-v1/search"
    search_collection: str = "products_v1_images"
    search_embed_device: str = "cuda"
    products_image_root: str = ""

    # /admin: the change log, hidden images and releases (small, kept in git) ...
    admin_dir: str = "graph/admin"
    # ... and uploaded images with their vectors (data, not in git).
    admin_data_dir: str = "data/admin"
    admin_upload_max_bytes: int = 10 * 1024 * 1024
    # CSV import: how new entities are classified and enriched (scripts/graph_classify.py,
    # graph_enrich.py): "agy" (local CLI), "api" (GEMINI_API_KEY in .env) or "off".
    admin_llm_engine: Literal["agy", "api", "off"] = "agy"
    admin_agy_path: str = str(Path.home() / "AppData/Local/agy/bin/agy.exe")
    admin_agy_classify_model: str = "gemini-3.8-flash-medium"
    admin_agy_enrich_model: str = "gemini-3.8-flash-low"
    admin_agy_timeout: int = 300

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
