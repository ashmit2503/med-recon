from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from app.db.mongo import get_database
from app.main import app


class FakeAggregateCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    async def to_list(self, length: int | None = None) -> list[dict[str, object]]:
        if length is None:
            return list(self._rows)
        return list(self._rows[:length])


class FakeAggregateCollection:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.rows = rows or []
        self.last_pipeline: list[dict[str, object]] | None = None

    def aggregate(self, pipeline: list[dict[str, object]]) -> FakeAggregateCursor:
        self.last_pipeline = pipeline
        return FakeAggregateCursor(self.rows)


class FakeAggregateDatabase:
    def __init__(
        self,
        patients_rows: list[dict[str, object]] | None = None,
        conflicts_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.collections = {
            "patients": FakeAggregateCollection(patients_rows),
            "conflicts": FakeAggregateCollection(conflicts_rows),
        }

    def __getitem__(self, collection_name: str) -> FakeAggregateCollection:
        if collection_name not in self.collections:
            self.collections[collection_name] = FakeAggregateCollection()
        return self.collections[collection_name]


@pytest.fixture
def test_client() -> TestClient:
    with patch("app.main.connect_to_mongo", new=AsyncMock()):
        with patch("app.main.close_mongo_connection", new=AsyncMock()):
            with TestClient(app) as client:
                yield client


def test_patients_with_unresolved_conflicts_aggregation_pipeline_and_response(
    test_client: TestClient,
) -> None:
    now = datetime(2026, 4, 25, tzinfo=timezone.utc)
    rows = [
        {
            "patient_id": "patient-1",
            "patient_key": "patient-001",
            "clinic_id": "clinic-a",
            "unresolved_conflict_count": 2,
            "latest_unresolved_conflict_at": now,
        },
        {
            "patient_id": "patient-2",
            "patient_key": "patient-002",
            "clinic_id": "clinic-a",
            "unresolved_conflict_count": 1,
            "latest_unresolved_conflict_at": now,
        },
    ]
    fake_database = FakeAggregateDatabase(patients_rows=rows)
    app.dependency_overrides[get_database] = lambda: fake_database

    response = test_client.get(
        "/api/analytics/clinics/clinic-a/patients-with-unresolved-conflicts?limit=50"
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["clinic_id"] == "clinic-a"
    assert payload["total_patients"] == 2
    assert payload["patients"][0]["unresolved_conflict_count"] == 2

    pipeline = fake_database["patients"].last_pipeline
    assert pipeline is not None
    assert pipeline[0] == {"$match": {"metadata.clinic_id": "clinic-a"}}
    lookup_stage = pipeline[1]["$lookup"]
    assert lookup_stage["from"] == "conflicts"
    assert lookup_stage["pipeline"][1] == {
        "$match": {"status": {"$in": ["open", "acknowledged"]}}
    }


def test_high_burden_clinic_aggregation_response_shape(test_client: TestClient) -> None:
    rows = [
        {
            "clinic_id": "clinic-a",
            "patients_with_conflict_burden": 4,
            "max_conflicts_for_single_patient": 7,
        },
        {
            "clinic_id": "clinic-b",
            "patients_with_conflict_burden": 2,
            "max_conflicts_for_single_patient": 3,
        },
    ]
    fake_database = FakeAggregateDatabase(conflicts_rows=rows)
    app.dependency_overrides[get_database] = lambda: fake_database

    response = test_client.get(
        "/api/analytics/clinics/conflicts/high-burden?days=30&minimum_conflicts=2"
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["window_days"] == 30
    assert payload["minimum_conflicts"] == 2
    assert payload["total_clinics"] == 2
    assert payload["clinics"][0]["clinic_id"] == "clinic-a"
    assert payload["clinics"][1]["patients_with_conflict_burden"] == 2

    pipeline = fake_database["conflicts"].last_pipeline
    assert pipeline is not None
    assert "$match" in pipeline[0]
    assert pipeline[1] == {"$group": {"_id": "$patient_id", "conflict_count": {"$sum": 1}}}
