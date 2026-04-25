from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.base import MongoDocument, utc_now
from app.models.medication_snapshot import MedicationSource


class ConflictType(StrEnum):
    DUPLICATE_THERAPY = "duplicate_therapy"
    DRUG_INTERACTION = "drug_interaction"
    DOSAGE_MISMATCH = "dosage_mismatch"
    ALLERGY_RISK = "allergy_risk"
    ADHERENCE_GAP = "adherence_gap"
    SOURCE_DISAGREEMENT = "source_disagreement"


class ConflictSeverity(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class ConflictStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class ConflictMedicationRef(BaseModel):
    medication_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=255)
    rxnorm_code: str | None = Field(default=None, max_length=32)
    source: MedicationSource | None = None
    source_version: str | None = Field(default=None, max_length=80)

    model_config = ConfigDict(str_strip_whitespace=True)


class ConflictEvidence(BaseModel):
    source: MedicationSource
    source_version: str = Field(min_length=1, max_length=80)
    detail: str = Field(min_length=1, max_length=500)

    model_config = ConfigDict(str_strip_whitespace=True)


class ConflictDocument(MongoDocument):
    patient_id: str = Field(min_length=1, max_length=64)
    snapshot_id: str = Field(min_length=1, max_length=64)
    conflict_key: str = Field(min_length=4, max_length=160)
    conflict_type: ConflictType
    severity: ConflictSeverity = ConflictSeverity.MODERATE
    status: ConflictStatus = ConflictStatus.OPEN
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    medications: list[ConflictMedicationRef] = Field(default_factory=list, min_length=1)
    evidence: list[ConflictEvidence] = Field(default_factory=list)
    recommended_action: str | None = Field(default=None, max_length=500)
    detected_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
    resolution_notes: str | None = Field(default=None, max_length=2000)
    assigned_to: str | None = Field(default=None, max_length=120)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_resolution_fields(self) -> "ConflictDocument":
        closed_states = {ConflictStatus.RESOLVED, ConflictStatus.DISMISSED}
        if self.status in closed_states and self.resolved_at is None:
            raise ValueError("resolved_at must be set when conflict is closed.")

        if self.status not in closed_states and self.resolved_at is not None:
            raise ValueError("resolved_at can only be set for closed conflicts.")

        return self
