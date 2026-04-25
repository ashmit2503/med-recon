from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import get_settings


class MongoState:
    def __init__(self) -> None:
        self.client: Optional[AsyncIOMotorClient] = None
        self.database: Optional[AsyncIOMotorDatabase] = None


mongo_state = MongoState()


async def connect_to_mongo() -> None:
    settings = get_settings()
    mongo_state.client = AsyncIOMotorClient(settings.mongodb_uri)
    mongo_state.database = mongo_state.client[settings.mongodb_database]


async def close_mongo_connection() -> None:
    if mongo_state.client is not None:
        mongo_state.client.close()

    mongo_state.client = None
    mongo_state.database = None


def get_database() -> AsyncIOMotorDatabase:
    if mongo_state.database is None:
        raise RuntimeError("MongoDB is not connected.")

    return mongo_state.database
