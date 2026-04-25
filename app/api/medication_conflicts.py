import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.db.mongo import get_database
from app.db.schema import CONFLICTS_COLLECTION, MEDICATION_SNAPSHOTS_COLLECTION, PATIENTS_COLLECTION
from app.models.base import utc_now
from app.models.conflict import ConflictDocument, ConflictStatus
from app.models.medication_snapshot import (
    MedicationItem,
    MedicationSnapshotDocument,
    MedicationSource,
    MedicationStatus,
    SourceMedicationVersion,
)
from app.models.patient import PatientDocument
from app.schemas.medication_api import (
    ConflictHistoryResponse,
    ConflictResolutionRequest,
    MedicationIngestItemRequest,
    MedicationIngestRequest,
    MedicationIngestResponse,
)
from app.services import ConflictDetectionEngine, MedicationNormalizationService

router = APIRouter()

conflict_engine = ConflictDetectionEngine()
normalization_service = MedicationNormalizationService()


@router.post(
    "/patients/{patient_key}/sources/{source}/medications/ingest",
    response_model=MedicationIngestResponse,
    summary="Ingest medications for a patient source",
)
async def ingest_medications(
    patient_key: str,
    source: MedicationSource,
    payload: MedicationIngestRequest,
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> MedicationIngestResponse:
    try:
        patient = await _get_patient_or_404(database, patient_key)
        current_snapshot = await _get_active_snapshot(database, patient)

        source_version = payload.source_version or _default_source_version(source)
        normalized_medications = _normalize_medications_for_ingest(payload.medications, source)

        source_versions_by_source: dict[MedicationSource, SourceMedicationVersion] = {}
        if current_snapshot is not None:
            for source_version_entry in current_snapshot.source_versions:
                source_versions_by_source[source_version_entry.source] = source_version_entry

        source_versions_by_source[source] = SourceMedicationVersion(
            source=source,
            version=source_version,
            medications=normalized_medications,
        )

        next_version = 1 if current_snapshot is None else current_snapshot.snapshot_version + 1
        snapshot_id = _build_snapshot_id(patient.id, next_version)
        source_versions = sorted(
            source_versions_by_source.values(),
            key=lambda value: value.source.value,
        )

        try:
            snapshot = MedicationSnapshotDocument(
                _id=snapshot_id,
                patient_id=patient.id,
                snapshot_version=next_version,
                source_versions=source_versions,
                merged_medications=_build_merged_medications(source_versions),
                metadata={
                    "ingested_source": source.value,
                    "ingested_source_version": source_version,
                },
            )
        except ValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Snapshot validation failed: {error}",
            ) from error

        snapshots_collection = database[MEDICATION_SNAPSHOTS_COLLECTION]
        try:
            await snapshots_collection.insert_one(snapshot.model_dump(by_alias=True))
        except DuplicateKeyError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Snapshot insert collided with an existing version. Retry ingest.",
            ) from error

        patients_collection = database[PATIENTS_COLLECTION]
        await patients_collection.update_one(
            {"_id": patient.id},
            {
                "$set": {
                    "active_snapshot_id": snapshot.id,
                    "updated_at": utc_now(),
                }
            },
        )

        detected_conflicts = conflict_engine.detect_conflicts(snapshot)
        persisted_conflicts, created_count, existing_count = await _upsert_conflicts(
            database,
            detected_conflicts,
        )

        return MedicationIngestResponse(
            patient_id=patient.id,
            patient_key=patient.patient_key,
            snapshot_id=snapshot.id,
            snapshot_version=snapshot.snapshot_version,
            ingested_source=source,
            source_version=source_version,
            conflict_count=len(persisted_conflicts),
            created_conflict_count=created_count,
            existing_conflict_count=existing_count,
            conflicts=persisted_conflicts,
        )
    except HTTPException:
        raise
    except PyMongoError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database operation failed while ingesting medications.",
        ) from error


@router.patch(
    "/patients/{patient_key}/conflicts/{conflict_id}/resolution",
    response_model=ConflictDocument,
    summary="Resolve or dismiss a conflict",
)
async def resolve_or_dismiss_conflict(
    patient_key: str,
    conflict_id: str,
    payload: ConflictResolutionRequest,
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> ConflictDocument:
    try:
        patient = await _get_patient_or_404(database, patient_key)

        conflicts_collection = database[CONFLICTS_COLLECTION]
        existing = await conflicts_collection.find_one(
            {"_id": conflict_id, "patient_id": patient.id}
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conflict was not found for the patient.",
            )

        status_value = (
            ConflictStatus.RESOLVED if payload.action == "resolve" else ConflictStatus.DISMISSED
        )
        now = utc_now()
        resolution_notes = (
            f"{payload.reason.strip()} (chosen_source={payload.chosen_source.value})"
        )

        await conflicts_collection.update_one(
            {"_id": conflict_id, "patient_id": patient.id},
            {
                "$set": {
                    "status": status_value.value,
                    "resolved_at": now,
                    "resolution_notes": resolution_notes,
                    "updated_at": now,
                    "metadata.resolution_source": payload.chosen_source.value,
                    "metadata.resolution_action": payload.action,
                }
            },
        )

        updated = await conflicts_collection.find_one(
            {"_id": conflict_id, "patient_id": patient.id}
        )
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conflict disappeared before update could be completed.",
            )

        return ConflictDocument.model_validate(updated)
    except HTTPException:
        raise
    except PyMongoError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database operation failed while updating conflict resolution.",
        ) from error


@router.get(
    "/patients/{patient_key}/conflicts",
    response_model=ConflictHistoryResponse,
    summary="Fetch conflict history for patient",
)
async def get_conflict_history(
    patient_key: str,
    status_filter: ConflictStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> ConflictHistoryResponse:
    try:
        patient = await _get_patient_or_404(database, patient_key)

        query: dict[str, str] = {"patient_id": patient.id}
        if status_filter is not None:
            query["status"] = status_filter.value

        conflicts_collection = database[CONFLICTS_COLLECTION]
        conflict_docs = await (
            conflicts_collection.find(query)
            .sort([("detected_at", -1)])
            .limit(limit)
            .to_list(length=limit)
        )

        conflicts = [ConflictDocument.model_validate(document) for document in conflict_docs]
        return ConflictHistoryResponse(
            patient_id=patient.id,
            patient_key=patient.patient_key,
            total=len(conflicts),
            status_filter=status_filter,
            conflicts=conflicts,
        )
    except HTTPException:
        raise
    except PyMongoError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database operation failed while retrieving conflict history.",
        ) from error


async def _get_patient_or_404(
    database: AsyncIOMotorDatabase,
    patient_key: str,
) -> PatientDocument:
    patients_collection = database[PATIENTS_COLLECTION]
    document = await patients_collection.find_one({"patient_key": patient_key})
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient was not found.",
        )

    return PatientDocument.model_validate(document)


async def _get_active_snapshot(
    database: AsyncIOMotorDatabase,
    patient: PatientDocument,
) -> MedicationSnapshotDocument | None:
    snapshots_collection = database[MEDICATION_SNAPSHOTS_COLLECTION]
    if patient.active_snapshot_id:
        active_document = await snapshots_collection.find_one(
            {"_id": patient.active_snapshot_id, "patient_id": patient.id}
        )
        if active_document is not None:
            return MedicationSnapshotDocument.model_validate(active_document)

    latest_documents = await (
        snapshots_collection.find({"patient_id": patient.id})
        .sort([("snapshot_version", -1)])
        .limit(1)
        .to_list(length=1)
    )
    if not latest_documents:
        return None

    return MedicationSnapshotDocument.model_validate(latest_documents[0])


def _default_source_version(source: MedicationSource) -> str:
    return f"{source.value}:{utc_now().isoformat()}"


def _normalize_medications_for_ingest(
    medications: list[MedicationIngestItemRequest],
    source: MedicationSource,
) -> list[MedicationItem]:
    normalized_items: list[MedicationItem] = []

    for index, medication in enumerate(medications):
        try:
            normalized = normalization_service.normalize(
                medication.name,
                dose=medication.dose,
                unit=medication.unit,
            )
        except (ValidationError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Medication normalization failed for item "
                    f"{index}: {error}"
                ),
            ) from error

        medication_id = medication.medication_id or _build_ingested_medication_id(
            source,
            index,
            normalized.canonical_name,
        )

        try:
            normalized_items.append(
                MedicationItem(
                    medication_id=medication_id,
                    name=normalized.canonical_name,
                    rxnorm_code=medication.rxnorm_code,
                    dose=normalized.canonical_dose,
                    route=medication.route,
                    frequency=medication.frequency,
                    status=medication.status,
                    source_medication_id=medication.source_medication_id,
                    indications=medication.indications,
                )
            )
        except ValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Medication payload failed validation at index {index}: {error}",
            ) from error

    return normalized_items


def _build_ingested_medication_id(
    source: MedicationSource,
    index: int,
    canonical_name: str,
) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", canonical_name.lower()).strip("-") or "medication"
    return f"{source.value}-{index + 1}-{slug}"[:80]


def _build_snapshot_id(patient_id: str, snapshot_version: int) -> str:
    return f"{patient_id}:v{snapshot_version}"[:64]


def _build_merged_medications(
    source_versions: list[SourceMedicationVersion],
) -> list[MedicationItem]:
    merged: dict[str, MedicationItem] = {}
    for source_version in source_versions:
        for medication in source_version.medications:
            key = _medication_merge_key(medication)
            existing = merged.get(key)
            if existing is None:
                merged[key] = medication
                continue

            if (
                existing.status != MedicationStatus.ACTIVE
                and medication.status == MedicationStatus.ACTIVE
            ):
                merged[key] = medication
                continue

            if existing.dose is None and medication.dose is not None:
                merged[key] = medication

    return sorted(merged.values(), key=lambda medication: medication.name.lower())


def _medication_merge_key(medication: MedicationItem) -> str:
    if medication.rxnorm_code:
        return f"rxnorm:{medication.rxnorm_code.strip().lower()}"

    return f"name:{medication.name.strip().lower()}"


async def _upsert_conflicts(
    database: AsyncIOMotorDatabase,
    conflicts: list[ConflictDocument],
) -> tuple[list[ConflictDocument], int, int]:
    if not conflicts:
        return [], 0, 0

    conflicts_collection = database[CONFLICTS_COLLECTION]
    created_count = 0
    existing_count = 0

    for conflict in conflicts:
        try:
            update_result = await conflicts_collection.update_one(
                {
                    "snapshot_id": conflict.snapshot_id,
                    "conflict_key": conflict.conflict_key,
                },
                {"$setOnInsert": conflict.model_dump(by_alias=True)},
                upsert=True,
            )
        except DuplicateKeyError:
            existing_count += 1
            continue

        if update_result.upserted_id is None:
            existing_count += 1
        else:
            created_count += 1

    persisted_docs = await (
        conflicts_collection.find({"snapshot_id": conflicts[0].snapshot_id})
        .sort([("detected_at", -1)])
        .to_list(length=1000)
    )
    persisted_conflicts = [ConflictDocument.model_validate(document) for document in persisted_docs]

    deduped_conflicts = _dedupe_conflicts_by_key(persisted_conflicts)
    return deduped_conflicts, created_count, existing_count


def _dedupe_conflicts_by_key(conflicts: list[ConflictDocument]) -> list[ConflictDocument]:
    seen: set[str] = set()
    deduped: list[ConflictDocument] = []

    for conflict in conflicts:
        unique_key = f"{conflict.snapshot_id}:{conflict.conflict_key}"
        if unique_key in seen:
            continue
        seen.add(unique_key)
        deduped.append(conflict)

    return deduped
