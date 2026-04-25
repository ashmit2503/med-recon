from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Medication Reconciliation & Conflict Reporting Service"
    environment: str = "development"
    mongodb_uri: str = Field(default="mongodb://localhost:27017")
    mongodb_database: str = "med_recon"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
