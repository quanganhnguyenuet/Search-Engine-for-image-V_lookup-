from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from visualseek_api import __version__
from visualseek_api.api.router import api_router
from visualseek_api.core.config import get_settings
from visualseek_api.core.errors import register_exception_handlers
from visualseek_api.core.logging import configure_logging
from visualseek_api.core.middleware import RequestContextMiddleware


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger(__name__)
    application.state.search = None
    application.state.admin = None
    if settings.search_enabled:
        from visualseek_api.application.admin import AdminService
        from visualseek_api.application.search import SearchService

        admin = AdminService(settings)
        search = SearchService.load(settings)
        admin.prepare(search)

        def reload() -> None:
            """After an admin release: load the new artifacts beside the running service, then
            swap; requests in flight finish on the old one."""
            fresh = SearchService.load(settings, reuse=application.state.search)
            admin.prepare(fresh)
            application.state.search = fresh
            logger.info("search_reloaded", graph_fingerprint=fresh.info["graph_fingerprint"])

        admin.on_reload = reload
        application.state.search = search
        application.state.admin = admin
        # CSV imports run on the current service (a release swaps it); unfinished ones resume.
        admin.imports.get_service = lambda: application.state.search
        admin.imports.resume()
    if not settings.require_login:
        logger.warning("login_not_required", detail="search is open to anyone who can reach it")
    elif not settings.supabase_url:
        logger.warning("auth_unconfigured", detail="set SUPABASE_URL; search returns 503")
    logger.info("application_started", environment=settings.app_env)
    yield
    logger.info("application_stopped")


def create_app() -> FastAPI:
    # In production the API can sit behind a public tunnel (web on Render -> API on the
    # laptop), so the interactive docs and the schema are not served.
    public = get_settings().app_env == "production"
    application = FastAPI(
        title="VisualSeek API",
        description="V-Lookup product image search.",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if public else "/docs",
        redoc_url=None if public else "/redoc",
        openapi_url=None if public else "/openapi.json",
    )
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )
    register_exception_handlers(application)
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
