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
    if settings.search_enabled:
        from visualseek_api.application.search import SearchService

        application.state.search = SearchService.load(settings)
    logger.info("application_started", environment=settings.app_env)
    yield
    logger.info("application_stopped")


def create_app() -> FastAPI:
    application = FastAPI(
        title="VisualSeek API",
        description="V-Lookup product image search.",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    register_exception_handlers(application)
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
