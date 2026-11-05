from fastapi import APIRouter

from visualseek_api.api.routes.health import router as health_router
from visualseek_api.api.routes.search import router as search_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["system"])
api_router.include_router(search_router, tags=["search"])
