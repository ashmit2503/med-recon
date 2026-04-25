from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.db.mongo import close_mongo_connection, connect_to_mongo

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await connect_to_mongo()
    yield
    await close_mongo_connection()


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)

app.include_router(api_router, prefix="/api")


@app.get("/", tags=["meta"], summary="Service metadata")
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "status": "ok",
        "environment": settings.environment,
    }
