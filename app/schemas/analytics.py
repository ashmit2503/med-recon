from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ClinicUnresolvedConflictPatient(BaseModel):
    patient_id: str = Field(min_length=1, max_length=64)
    patient_key: str = Field(min_length=1, max_length=64)
    clinic_id: str = Field(min_length=1, max_length=120)
    unresolved_conflict_count: int = Field(ge=1)
    latest_unresolved_conflict_at: datetime | None = None


class ClinicUnresolvedConflictPatientsResponse(BaseModel):
    clinic_id: str = Field(min_length=1, max_length=120)
    total_patients: int = Field(ge=0)
    patients: list[ClinicUnresolvedConflictPatient] = Field(default_factory=list)


class ClinicConflictBurdenCount(BaseModel):
    clinic_id: str = Field(min_length=1, max_length=120)
    patients_with_conflict_burden: int = Field(ge=1)
    max_conflicts_for_single_patient: int = Field(ge=1)


class ClinicConflictBurdenResponse(BaseModel):
    window_days: int = Field(ge=1)
    minimum_conflicts: int = Field(ge=2)
    generated_at: datetime
    total_clinics: int = Field(ge=0)
    clinics: list[ClinicConflictBurdenCount] = Field(default_factory=list)

    model_config = ConfigDict(str_strip_whitespace=True)
