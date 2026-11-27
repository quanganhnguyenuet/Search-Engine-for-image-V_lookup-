from fastapi import APIRouter, Depends

from visualseek_api.api.routes.admin import router as admin_router
from visualseek_api.api.routes.graph import router as graph_router
from visualseek_api.api.routes.health import router as health_router
from visualseek_api.api.routes.images import router as images_router
from visualseek_api.api.routes.search import router as search_router
from visualseek_api.core.auth import current_user

api_router = APIRouter()
api_router.include_router(health_router, tags=["system"])
api_router.include_router(images_router, tags=["images"])

# Everything registered with this dependency needs a signed-in user.
signed_in = [Depends(current_user)]
api_router.include_router(search_router, tags=["search"], dependencies=signed_in)
api_router.include_router(graph_router, tags=["graph"], dependencies=signed_in)
# require_admin is declared on every admin route (it also hands the handler the admin's identity).
api_router.include_router(admin_router, prefix="/admin", tags=["admin"])
