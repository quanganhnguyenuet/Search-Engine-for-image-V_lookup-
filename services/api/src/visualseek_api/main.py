from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from visualseek_api import __version__
from visualseek_api.api.router import api_router
from visualseek_api.core.config import get_settings
from visualseek_api.core.errors import register_exception_handlers
from visualseek_api.core.logging import configure_logging
from visualseek_api.core.middleware import RequestContextMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger(__name__)
    logger.info("application_started", environment=settings.app_env)
    yield
    logger.info("application_stopped")


def create_app() -> FastAPI:
    application = FastAPI(
        title="VisualSeek API",
        description="VietNam Culture image retrieval with SigLIP2 and grounded RAG.",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(RequestContextMiddleware)
    register_exception_handlers(application)
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
