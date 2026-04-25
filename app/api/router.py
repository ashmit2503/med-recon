from fastapi import APIRouter

from app.api.analytics import router as analytics_router
from app.api.health import router as health_router
from app.api.medication_conflicts import router as medication_conflict_router

api_router = APIRouter()
api_router.include_router(analytics_router, tags=["analytics"])
api_router.include_router(health_router, tags=["health"])
api_router.include_router(medication_conflict_router, tags=["medications", "conflicts"])
