from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.conflict import ConflictDocument, ConflictStatus
from app.models.medication_snapshot import MedicationSource, MedicationStatus


class MedicationIngestItemRequest(BaseModel):
    medication_id: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=1, max_length=255)
    rxnorm_code: str | None = Field(default=None, max_length=32)
    dose: str | int | float | None = Field(default=None)
    unit: str | None = Field(default=None, max_length=20)
    route: str | None = Field(default=None, max_length=80)
    frequency: str | None = Field(default=None, max_length=120)
    status: MedicationStatus = MedicationStatus.ACTIVE
    source_medication_id: str | None = Field(default=None, max_length=120)
    indications: list[str] = Field(default_factory=list)

    model_config = ConfigDict(str_strip_whitespace=True)


class MedicationIngestRequest(BaseModel):
    source_version: str | None = Field(default=None, max_length=80)
    medications: list[MedicationIngestItemRequest] = Field(min_length=1)


class MedicationIngestResponse(BaseModel):
    patient_id: str
    patient_key: str
    snapshot_id: str
    snapshot_version: int
    ingested_source: MedicationSource
    source_version: str
    conflict_count: int
    created_conflict_count: int
    existing_conflict_count: int
    conflicts: list[ConflictDocument]


class ConflictResolutionRequest(BaseModel):
    action: Literal["resolve", "dismiss"]
    reason: str = Field(min_length=3, max_length=500)
    chosen_source: MedicationSource


class ConflictHistoryResponse(BaseModel):
    patient_id: str
    patient_key: str
    total: int
    status_filter: ConflictStatus | None = None
    conflicts: list[ConflictDocument]
