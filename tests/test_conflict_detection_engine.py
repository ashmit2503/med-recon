from app.models.conflict import ConflictType
from app.models.medication_snapshot import MedicationSnapshotDocument
from app.services import ConflictDetectionEngine


def _medication(
    medication_id: str,
    name: str,
    dose: str | None,
    status: str = "active",
    rxnorm_code: str | None = None,
) -> dict[str, str]:
    payload: dict[str, str] = {
        "medication_id": medication_id,
        "name": name,
        "status": status,
    }
    if dose is not None:
        payload["dose"] = dose
    if rxnorm_code is not None:
        payload["rxnorm_code"] = rxnorm_code

    return payload


def _source(
    source: str,
    version: str,
    medications: list[dict[str, str]],
) -> dict[str, object]:
    return {"source": source, "version": version, "medications": medications}


def _snapshot(
    source_versions: list[dict[str, object]],
    snapshot_id: str = "snapshot-001",
) -> MedicationSnapshotDocument:
    return MedicationSnapshotDocument(
        _id=snapshot_id,
        patient_id="patient-001",
        snapshot_version=7,
        source_versions=source_versions,
    )


def test_detect_conflicts_returns_empty_for_single_source_snapshot() -> None:
    snapshot = _snapshot(
        [
            _source(
                "ehr",
                "ehr-v1",
                [
                    _medication("med-1", "Lisinopril", "10 mg", status="active"),
                    _medication("med-2", "Metformin", "500 mg", status="active"),
                ],
            )
        ]
    )

    engine = ConflictDetectionEngine()
    conflicts = engine.detect_conflicts(snapshot)

    assert conflicts == []


def test_detect_conflicts_returns_empty_when_all_sources_agree() -> None:
    snapshot = _snapshot(
        [
            _source(
                "ehr",
                "ehr-v3",
                [
                    _medication("med-1", "Lisinopril", "10 mg", status="active"),
                    _medication("med-2", "Metformin", "500 mg", status="active"),
                ],
            ),
            _source(
                "pharmacy_dispense",
                "rx-v9",
                [
                    _medication("med-9", "Lisinopril", "0.01 g", status="active"),
                    _medication("med-10", "Metformin", "500mg", status="active"),
                ],
            ),
        ]
    )

    engine = ConflictDetectionEngine()
    conflicts = engine.detect_conflicts(snapshot)

    assert conflicts == []


def test_detect_conflicts_tolerates_missing_or_malformed_dose_fields() -> None:
    snapshot = _snapshot(
        [
            _source(
                "ehr",
                "ehr-v4",
                [
                    _medication("med-1", "Warfarin", None, status="active"),
                    _medication("med-2", "500 mg tablet", "500 mg", status="active"),
                ],
            ),
            _source(
                "insurance_claims",
                "claim-v2",
                [
                    _medication("med-11", "Warfarin", "five mg", status="active"),
                    _medication("med-12", "Acetaminophen", None, status="active"),
                ],
            ),
        ]
    )

    engine = ConflictDetectionEngine()
    conflicts = engine.detect_conflicts(snapshot)

    assert conflicts == []


def test_detect_conflicts_finds_three_way_disagreement_patterns() -> None:
    snapshot = _snapshot(
        [
            _source(
                "ehr",
                "ehr-v8",
                [
                    _medication("med-1", "Lisinopril", "10 mg", status="active"),
                    _medication("med-2", "Warfarin", "5 mg", status="active"),
                ],
            ),
            _source(
                "pharmacy_dispense",
                "rx-v15",
                [
                    _medication("med-9", "Lisinopril", "20 mg", status="active"),
                    _medication("med-10", "Ibuprofen", "200 mg", status="active"),
                ],
            ),
            _source(
                "patient_reported",
                "patient-v2",
                [
                    _medication("med-11", "Lisinopril", "10 mg", status="discontinued"),
                ],
            ),
        ],
        snapshot_id="snapshot-three-way",
    )

    engine = ConflictDetectionEngine()
    conflicts = engine.detect_conflicts(snapshot)

    conflict_types = {conflict.conflict_type for conflict in conflicts}
    assert conflict_types == {
        ConflictType.DOSAGE_MISMATCH,
        ConflictType.DRUG_INTERACTION,
        ConflictType.SOURCE_DISAGREEMENT,
    }

    dosage_conflict = next(
        conflict for conflict in conflicts if conflict.conflict_type == ConflictType.DOSAGE_MISMATCH
    )
    assert "10 mg" in dosage_conflict.description
    assert "20 mg" in dosage_conflict.description

    source_conflict = next(
        conflict
        for conflict in conflicts
        if conflict.conflict_type == ConflictType.SOURCE_DISAGREEMENT
    )
    assert any("discontinued" in evidence.detail for evidence in source_conflict.evidence)

    interaction_conflict = next(
        conflict for conflict in conflicts if conflict.conflict_type == ConflictType.DRUG_INTERACTION
    )
    interaction_names = {medication.name for medication in interaction_conflict.medications}
    assert {"warfarin", "ibuprofen"}.issubset(interaction_names)


def test_detect_conflicts_includes_conflict_metadata_for_review_workflow() -> None:
    snapshot = _snapshot(
        [
            _source(
                "ehr",
                "ehr-v9",
                [_medication("med-1", "Lisinopril", "10 mg", status="active")],
            ),
            _source(
                "pharmacy_dispense",
                "rx-v16",
                [_medication("med-7", "Lisinopril", "30 mg", status="active")],
            ),
        ],
        snapshot_id="snapshot-meta",
    )

    engine = ConflictDetectionEngine()
    conflicts = engine.detect_conflicts(snapshot)

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.conflict_type == ConflictType.DOSAGE_MISMATCH
    assert conflict.metadata["medication_key"].startswith("name:lisinopril")
    assert conflict.metadata["dose_values"] == "10 mg|30 mg"
