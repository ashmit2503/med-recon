from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    ConflictDocument,
    ConflictStatus,
    ConflictType,
    MedicationSnapshotDocument,
    PatientDocument,
)


def _medication_item(medication_id: str, name: str) -> dict[str, str]:
    return {"medication_id": medication_id, "name": name}


def test_patient_document_rejects_duplicate_identifiers() -> None:
    with pytest.raises(ValidationError):
        PatientDocument(
            patient_key="patient-001",
            demographics={
                "given_name": "Avery",
                "family_name": "Nguyen",
                "date_of_birth": date(1985, 4, 2),
                "sex": "female",
            },
            identifiers=[
                {"system": "MRN", "value": "12345"},
                {"system": "mrn", "value": "12345"},
            ],
        )


def test_snapshot_rejects_duplicate_source_versions() -> None:
    with pytest.raises(ValidationError):
        MedicationSnapshotDocument(
            patient_id="patient-001",
            snapshot_version=1,
            source_versions=[
                {"source": "ehr", "version": "ehr-v14"},
                {"source": "ehr", "version": "ehr-v15"},
            ],
        )


def test_snapshot_exposes_source_version_map() -> None:
    snapshot = MedicationSnapshotDocument(
        patient_id="patient-001",
        snapshot_version=3,
        source_versions=[
            {
                "source": "ehr",
                "version": "ehr-v23",
                "medications": [_medication_item("med-1", "Atorvastatin")],
            },
            {
                "source": "pharmacy_dispense",
                "version": "rx-v8",
                "medications": [_medication_item("med-2", "Lisinopril")],
            },
        ],
        merged_medications=[
            _medication_item("med-1", "Atorvastatin"),
            _medication_item("med-2", "Lisinopril"),
        ],
    )

    assert snapshot.source_version_map == {
        "ehr": "ehr-v23",
        "pharmacy_dispense": "rx-v8",
    }


def test_conflict_requires_resolved_at_for_closed_status() -> None:
    with pytest.raises(ValidationError):
        ConflictDocument(
            patient_id="patient-001",
            snapshot_id="snapshot-001",
            conflict_key="snapshot-001:drug_interaction:med-1-med-2",
            conflict_type=ConflictType.DRUG_INTERACTION,
            status=ConflictStatus.RESOLVED,
            title="Potential interaction detected",
            description="Atorvastatin and clarithromycin overlap.",
            medications=[
                _medication_item("med-1", "Atorvastatin"),
                _medication_item("med-2", "Clarithromycin"),
            ],
        )


def test_conflict_accepts_closed_status_with_resolved_timestamp() -> None:
    resolved_at = datetime(2026, 4, 25, tzinfo=timezone.utc)

    conflict = ConflictDocument(
        patient_id="patient-001",
        snapshot_id="snapshot-001",
        conflict_key="snapshot-001:source_disagreement:med-3",
        conflict_type=ConflictType.SOURCE_DISAGREEMENT,
        status=ConflictStatus.DISMISSED,
        resolved_at=resolved_at,
        title="Source disagreement accepted",
        description="Provider retained EHR dosage over patient-reported dosage.",
        medications=[_medication_item("med-3", "Metformin")],
    )

    assert conflict.resolved_at == resolved_at
