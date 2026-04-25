from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.base import MongoDocument, utc_now


class MedicationSource(StrEnum):
    EHR = "ehr"
    PHARMACY_DISPENSE = "pharmacy_dispense"
    INSURANCE_CLAIMS = "insurance_claims"
    PATIENT_REPORTED = "patient_reported"


class MedicationStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISCONTINUED = "discontinued"
    UNKNOWN = "unknown"


class MedicationItem(BaseModel):
    medication_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=255)
    rxnorm_code: str | None = Field(default=None, max_length=32)
    dose: str | None = Field(default=None, max_length=120)
    route: str | None = Field(default=None, max_length=80)
    frequency: str | None = Field(default=None, max_length=120)
    status: MedicationStatus = MedicationStatus.ACTIVE
    source_medication_id: str | None = Field(default=None, max_length=120)
    indications: list[str] = Field(default_factory=list)

    model_config = ConfigDict(str_strip_whitespace=True)


class SourceMedicationVersion(BaseModel):
    source: MedicationSource
    version: str = Field(min_length=1, max_length=80)
    captured_at: datetime = Field(default_factory=utc_now)
    medications: list[MedicationItem] = Field(default_factory=list)
    checksum: str | None = Field(default=None, max_length=128)


class MedicationSnapshotDocument(MongoDocument):
    patient_id: str = Field(min_length=1, max_length=64)
    snapshot_version: int = Field(ge=1)
    source_versions: list[SourceMedicationVersion] = Field(default_factory=list, min_length=1)
    merged_medications: list[MedicationItem] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)
    reconciliation_notes: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source_versions(self) -> "MedicationSnapshotDocument":
        seen_sources: set[str] = set()
        for entry in self.source_versions:
            source_key = entry.source.value
            if source_key in seen_sources:
                raise ValueError("source_versions must have at most one entry per source.")
            seen_sources.add(source_key)

        return self

    @property
    def source_version_map(self) -> dict[str, str]:
        return {entry.source.value: entry.version for entry in self.source_versions}
