from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from app.db.mongo import get_database
from app.db.schema import CONFLICTS_COLLECTION, MEDICATION_SNAPSHOTS_COLLECTION, PATIENTS_COLLECTION
from app.main import app
from app.models.conflict import ConflictDocument, ConflictType
from app.models.medication_snapshot import MedicationSnapshotDocument
from app.models.patient import PatientDocument


class FakeInsertResult:
    def __init__(self, inserted_id: str | None) -> None:
        self.inserted_id = inserted_id


class FakeUpdateResult:
    def __init__(
        self,
        matched_count: int = 0,
        modified_count: int = 0,
        upserted_id: str | None = None,
    ) -> None:
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class FakeCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self._documents = documents
        self._limit: int | None = None

    def sort(self, sort_spec: list[tuple[str, int]]) -> "FakeCursor":
        key, direction = sort_spec[0]
        reverse = direction < 0
        self._documents.sort(key=lambda item: _get_nested(item, key), reverse=reverse)
        return self

    def limit(self, value: int) -> "FakeCursor":
        self._limit = value
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, object]]:
        documents = self._documents
        if self._limit is not None:
            documents = documents[: self._limit]
        if length is not None:
            documents = documents[:length]

        return [deepcopy(document) for document in documents]


class FakeCollection:
    def __init__(self) -> None:
        self.documents: list[dict[str, object]] = []

    async def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        for document in self.documents:
            if _matches(document, query):
                return deepcopy(document)

        return None

    async def insert_one(self, document: dict[str, object]) -> FakeInsertResult:
        if "_id" in document:
            for existing in self.documents:
                if existing.get("_id") == document["_id"]:
                    raise DuplicateKeyError("duplicate _id")

        self.documents.append(deepcopy(document))
        return FakeInsertResult(str(document.get("_id")) if document.get("_id") else None)

    async def update_one(
        self,
        query: dict[str, object],
        update: dict[str, dict[str, object]],
        upsert: bool = False,
    ) -> FakeUpdateResult:
        for document in self.documents:
            if not _matches(document, query):
                continue

            if "$set" in update:
                for key, value in update["$set"].items():
                    _set_nested(document, key, value)

            return FakeUpdateResult(matched_count=1, modified_count=1)

        if not upsert:
            return FakeUpdateResult(matched_count=0, modified_count=0)

        new_document = deepcopy(query)
        if "$setOnInsert" in update:
            for key, value in update["$setOnInsert"].items():
                _set_nested(new_document, key, value)

        if "$set" in update:
            for key, value in update["$set"].items():
                _set_nested(new_document, key, value)

        if "_id" not in new_document:
            new_document["_id"] = f"generated-{len(self.documents) + 1}"

        self.documents.append(new_document)
        return FakeUpdateResult(
            matched_count=0,
            modified_count=0,
            upserted_id=str(new_document["_id"]),
        )

    def find(self, query: dict[str, object]) -> FakeCursor:
        matches = [document for document in self.documents if _matches(document, query)]
        return FakeCursor([deepcopy(document) for document in matches])


class FakeDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, collection_name: str) -> FakeCollection:
        if collection_name not in self._collections:
            self._collections[collection_name] = FakeCollection()
        return self._collections[collection_name]


@pytest.fixture
def client_and_database() -> tuple[TestClient, FakeDatabase]:
    database = FakeDatabase()
    app.dependency_overrides[get_database] = lambda: database

    with patch("app.main.connect_to_mongo", new=AsyncMock()):
        with patch("app.main.close_mongo_connection", new=AsyncMock()):
            with TestClient(app) as client:
                yield client, database

    app.dependency_overrides.clear()


def _get_nested(document: dict[str, object], path: str) -> object:
    current: object = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]

    return current


def _set_nested(document: dict[str, object], path: str, value: object) -> None:
    parts = path.split(".")
    current: dict[str, object] = document
    for part in parts[:-1]:
        existing = current.get(part)
        if not isinstance(existing, dict):
            current[part] = {}
        current = current[part]  # type: ignore[assignment]

    current[parts[-1]] = value


def _matches(document: dict[str, object], query: dict[str, object]) -> bool:
    for key, value in query.items():
        if _get_nested(document, key) != value:
            return False

    return True


def _seed_patient(database: FakeDatabase, patient_key: str = "patient-001") -> PatientDocument:
    patient = PatientDocument(
        _id="patient-1",
        patient_key=patient_key,
        demographics={
            "given_name": "Avery",
            "family_name": "Nguyen",
            "date_of_birth": "1985-04-02",
            "sex": "female",
        },
        identifiers=[{"system": "MRN", "value": "12345"}],
    )
    database[PATIENTS_COLLECTION].documents.append(patient.model_dump(by_alias=True))
    return patient


def _seed_snapshot(
    database: FakeDatabase,
    patient_id: str,
    snapshot_version: int,
    source: str,
    source_version: str,
    medications: list[dict[str, str]],
) -> MedicationSnapshotDocument:
    snapshot = MedicationSnapshotDocument(
        _id=f"{patient_id}:v{snapshot_version}",
        patient_id=patient_id,
        snapshot_version=snapshot_version,
        source_versions=[
            {
                "source": source,
                "version": source_version,
                "medications": medications,
            }
        ],
    )
    database[MEDICATION_SNAPSHOTS_COLLECTION].documents.append(snapshot.model_dump(by_alias=True))
    return snapshot


def test_ingest_returns_404_for_missing_patient(client_and_database: tuple[TestClient, FakeDatabase]) -> None:
    client, _ = client_and_database

    response = client.post(
        "/api/patients/missing-patient/sources/ehr/medications/ingest",
        json={
            "medications": [
                {
                    "name": "Warfarin",
                    "dose": "5 mg",
                }
            ]
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Patient was not found."


def test_ingest_returns_422_for_invalid_medication_units(
    client_and_database: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = client_and_database
    _seed_patient(database)

    response = client.post(
        "/api/patients/patient-001/sources/ehr/medications/ingest",
        json={
            "medications": [
                {
                    "name": "Warfarin",
                    "dose": "5",
                    "unit": "drops",
                }
            ]
        },
    )

    assert response.status_code == 422
    assert "Medication normalization failed" in response.json()["detail"]


def test_ingest_triggers_normalization_and_conflict_detection(
    client_and_database: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = client_and_database
    patient = _seed_patient(database)

    snapshot_v1 = _seed_snapshot(
        database,
        patient_id=patient.id,
        snapshot_version=1,
        source="ehr",
        source_version="ehr-v1",
        medications=[
            {
                "medication_id": "med-ehr-1",
                "name": "Warfarin",
                "dose": "5 mg",
                "status": "active",
            }
        ],
    )
    database[PATIENTS_COLLECTION].documents[0]["active_snapshot_id"] = snapshot_v1.id

    response = client.post(
        "/api/patients/patient-001/sources/pharmacy_dispense/medications/ingest",
        json={
            "source_version": "rx-v2",
            "medications": [
                {
                    "name": "Advil 200 mg tablet",
                    "dose": "200 mg",
                    "status": "active",
                },
                {
                    "name": "Warfarin",
                    "dose": "10 mg",
                    "status": "active",
                },
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_version"] == 2
    assert body["created_conflict_count"] == 2
    assert body["existing_conflict_count"] == 0

    conflict_types = {entry["conflict_type"] for entry in body["conflicts"]}
    assert conflict_types == {"dosage_mismatch", "drug_interaction"}

    snapshot_documents = database[MEDICATION_SNAPSHOTS_COLLECTION].documents
    latest_snapshot = next(document for document in snapshot_documents if document["_id"] == "patient-1:v2")
    pharmacy_entry = next(
        entry for entry in latest_snapshot["source_versions"] if entry["source"] == "pharmacy_dispense"
    )
    normalized_names = {medication["name"] for medication in pharmacy_entry["medications"]}
    assert "ibuprofen" in normalized_names


def test_ingest_handles_preexisting_duplicate_conflict_records(
    client_and_database: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = client_and_database
    patient = _seed_patient(database)

    snapshot_v1 = _seed_snapshot(
        database,
        patient_id=patient.id,
        snapshot_version=1,
        source="ehr",
        source_version="ehr-v1",
        medications=[
            {
                "medication_id": "med-ehr-1",
                "name": "Warfarin",
                "dose": "5 mg",
                "status": "active",
            }
        ],
    )
    database[PATIENTS_COLLECTION].documents[0]["active_snapshot_id"] = snapshot_v1.id

    preexisting_conflict = ConflictDocument(
        _id="conflict-existing",
        patient_id=patient.id,
        snapshot_id="patient-1:v2",
        conflict_key="patient-1:v2:dosage_mismatch:name:warfarin",
        conflict_type=ConflictType.DOSAGE_MISMATCH,
        title="Dose mismatch for warfarin",
        description="Preexisting conflict for idempotency check.",
        medications=[{"medication_id": "med-ehr-1", "name": "warfarin"}],
    )
    database[CONFLICTS_COLLECTION].documents.append(preexisting_conflict.model_dump(by_alias=True))

    response = client.post(
        "/api/patients/patient-001/sources/pharmacy_dispense/medications/ingest",
        json={
            "source_version": "rx-v3",
            "medications": [
                {
                    "name": "Warfarin",
                    "dose": "10 mg",
                    "status": "active",
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conflict_count"] == 1
    assert body["existing_conflict_count"] >= 1


def test_resolve_or_dismiss_conflict_persists_reason_and_source(
    client_and_database: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = client_and_database
    patient = _seed_patient(database)

    conflict = ConflictDocument(
        _id="conflict-1",
        patient_id=patient.id,
        snapshot_id="patient-1:v1",
        conflict_key="patient-1:v1:source_disagreement:name:warfarin",
        conflict_type=ConflictType.SOURCE_DISAGREEMENT,
        title="Source disagreement for warfarin",
        description="Warfarin status differs across source feeds.",
        medications=[{"medication_id": "med-1", "name": "warfarin"}],
    )
    database[CONFLICTS_COLLECTION].documents.append(conflict.model_dump(by_alias=True))

    response = client.patch(
        "/api/patients/patient-001/conflicts/conflict-1/resolution",
        json={
            "action": "dismiss",
            "reason": "Pharmacy data source was stale.",
            "chosen_source": "ehr",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "dismissed"
    assert "chosen_source=ehr" in body["resolution_notes"]
    assert body["metadata"]["resolution_source"] == "ehr"
    assert body["metadata"]["resolution_action"] == "dismiss"


def test_conflict_history_returns_sorted_and_filterable_data(
    client_and_database: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = client_and_database
    patient = _seed_patient(database)

    older = datetime.now(timezone.utc) - timedelta(days=2)
    newer = datetime.now(timezone.utc)

    database[CONFLICTS_COLLECTION].documents.extend(
        [
            ConflictDocument(
                _id="conflict-old",
                patient_id=patient.id,
                snapshot_id="patient-1:v1",
                conflict_key="patient-1:v1:source_disagreement:name:warfarin",
                conflict_type=ConflictType.SOURCE_DISAGREEMENT,
                title="Older conflict",
                description="Older record",
                medications=[{"medication_id": "med-1", "name": "warfarin"}],
                detected_at=older,
            ).model_dump(by_alias=True),
            ConflictDocument(
                _id="conflict-new",
                patient_id=patient.id,
                snapshot_id="patient-1:v2",
                conflict_key="patient-1:v2:dosage_mismatch:name:warfarin",
                conflict_type=ConflictType.DOSAGE_MISMATCH,
                title="Newer conflict",
                description="Newer record",
                medications=[{"medication_id": "med-2", "name": "warfarin"}],
                detected_at=newer,
            ).model_dump(by_alias=True),
        ]
    )

    history_response = client.get("/api/patients/patient-001/conflicts?limit=20")
    assert history_response.status_code == 200
    history_body = history_response.json()
    assert history_body["total"] == 2
    assert history_body["conflicts"][0]["_id"] == "conflict-new"
    assert history_body["conflicts"][1]["_id"] == "conflict-old"

    filtered_response = client.get("/api/patients/patient-001/conflicts?status=open")
    assert filtered_response.status_code == 200
    filtered_body = filtered_response.json()
    assert filtered_body["total"] == 2


def test_conflict_history_returns_404_for_missing_patient(
    client_and_database: tuple[TestClient, FakeDatabase],
) -> None:
    client, _ = client_and_database

    response = client.get("/api/patients/missing-patient/conflicts")

    assert response.status_code == 404
    assert response.json()["detail"] == "Patient was not found."
