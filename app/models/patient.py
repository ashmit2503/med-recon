from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.base import MongoDocument


class PatientSex(StrEnum):
    FEMALE = "female"
    MALE = "male"
    OTHER = "other"
    UNKNOWN = "unknown"


class PatientStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DECEASED = "deceased"


class PatientIdentifier(BaseModel):
    system: str = Field(min_length=2, max_length=50)
    value: str = Field(min_length=1, max_length=120)

    model_config = ConfigDict(str_strip_whitespace=True)


class PatientDemographics(BaseModel):
    given_name: str = Field(min_length=1, max_length=100)
    family_name: str = Field(min_length=1, max_length=100)
    date_of_birth: date
    sex: PatientSex = PatientSex.UNKNOWN

    model_config = ConfigDict(str_strip_whitespace=True)


class PatientDocument(MongoDocument):
    patient_key: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    status: PatientStatus = PatientStatus.ACTIVE
    demographics: PatientDemographics
    identifiers: list[PatientIdentifier] = Field(default_factory=list, min_length=1)
    allergies: list[str] = Field(default_factory=list)
    active_snapshot_id: str | None = Field(default=None, max_length=64)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("allergies")
    @classmethod
    def normalize_allergies(cls, allergies: list[str]) -> list[str]:
        normalized = {entry.strip() for entry in allergies if entry.strip()}
        return sorted(normalized)

    @model_validator(mode="after")
    def validate_identifiers(self) -> "PatientDocument":
        seen: set[tuple[str, str]] = set()
        for identifier in self.identifiers:
            key = (identifier.system.lower(), identifier.value.lower())
            if key in seen:
                raise ValueError("Duplicate identifier system/value pairs are not allowed.")
            seen.add(key)

        return self
