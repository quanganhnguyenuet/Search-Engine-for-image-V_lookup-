from fastapi import APIRouter

from visualseek_api import __version__
from visualseek_api.schemas.health import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse, summary="Check service health")
async def health_check() -> HealthResponse:
    return HealthResponse(service="visualseek-api", status="ok", version=__version__)

