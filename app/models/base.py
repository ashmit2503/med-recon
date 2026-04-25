from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_document_id() -> str:
    return str(uuid4())


class MongoDocument(BaseModel):
    id: str = Field(default_factory=new_document_id, alias="_id")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = ConfigDict(
        populate_by_name=True,
        validate_assignment=True,
        str_strip_whitespace=True,
    )

    def touch(self) -> None:
        self.updated_at = utc_now()
