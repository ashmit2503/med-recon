from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.mongo import get_database
from app.db.schema import CONFLICTS_COLLECTION, PATIENTS_COLLECTION
from app.models.base import utc_now
from app.models.conflict import ConflictStatus
from app.schemas.analytics import (
    ClinicConflictBurdenCount,
    ClinicConflictBurdenResponse,
    ClinicUnresolvedConflictPatient,
    ClinicUnresolvedConflictPatientsResponse,
)

router = APIRouter()

_UNRESOLVED_STATUSES = [ConflictStatus.OPEN.value, ConflictStatus.ACKNOWLEDGED.value]


@router.get(
    "/analytics/clinics/{clinic_id}/patients-with-unresolved-conflicts",
    response_model=ClinicUnresolvedConflictPatientsResponse,
    summary="List clinic patients with unresolved conflicts",
)
async def list_patients_with_unresolved_conflicts(
    clinic_id: str,
    limit: int = Query(default=200, ge=1, le=2000),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> ClinicUnresolvedConflictPatientsResponse:
    pipeline = [
        {"$match": {"metadata.clinic_id": clinic_id}},
        {
            "$lookup": {
                "from": CONFLICTS_COLLECTION,
                "let": {"patientId": "$_id"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$patient_id", "$$patientId"]}}},
                    {"$match": {"status": {"$in": _UNRESOLVED_STATUSES}}},
                    {"$project": {"_id": 1, "detected_at": 1}},
                ],
                "as": "unresolved_conflicts",
            }
        },
        {
            "$addFields": {
                "unresolved_conflict_count": {"$size": "$unresolved_conflicts"},
                "latest_unresolved_conflict_at": {"$max": "$unresolved_conflicts.detected_at"},
            }
        },
        {"$match": {"unresolved_conflict_count": {"$gte": 1}}},
        {
            "$project": {
                "_id": 0,
                "patient_id": "$_id",
                "patient_key": 1,
                "clinic_id": "$metadata.clinic_id",
                "unresolved_conflict_count": 1,
                "latest_unresolved_conflict_at": 1,
            }
        },
        {"$sort": {"unresolved_conflict_count": -1, "patient_key": 1}},
        {"$limit": limit},
    ]

    rows = await (
        database[PATIENTS_COLLECTION].aggregate(pipeline).to_list(length=limit)
    )
    patients = [ClinicUnresolvedConflictPatient.model_validate(row) for row in rows]

    return ClinicUnresolvedConflictPatientsResponse(
        clinic_id=clinic_id,
        total_patients=len(patients),
        patients=patients,
    )


@router.get(
    "/analytics/clinics/conflicts/high-burden",
    response_model=ClinicConflictBurdenResponse,
    summary="Count clinics with high patient conflict burden",
)
async def count_high_burden_clinics(
    days: int = Query(default=30, ge=1, le=365),
    minimum_conflicts: int = Query(default=2, ge=2, le=100),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> ClinicConflictBurdenResponse:
    window_start = utc_now() - timedelta(days=days)

    pipeline = [
        {"$match": {"detected_at": {"$gte": window_start}}},
        {"$group": {"_id": "$patient_id", "conflict_count": {"$sum": 1}}},
        {"$match": {"conflict_count": {"$gte": minimum_conflicts}}},
        {
            "$lookup": {
                "from": PATIENTS_COLLECTION,
                "localField": "_id",
                "foreignField": "_id",
                "as": "patient",
            }
        },
        {"$unwind": "$patient"},
        {
            "$group": {
                "_id": "$patient.metadata.clinic_id",
                "patients_with_conflict_burden": {"$sum": 1},
                "max_conflicts_for_single_patient": {"$max": "$conflict_count"},
            }
        },
        {"$match": {"_id": {"$nin": [None, ""]}}},
        {
            "$project": {
                "_id": 0,
                "clinic_id": "$_id",
                "patients_with_conflict_burden": 1,
                "max_conflicts_for_single_patient": 1,
            }
        },
        {"$sort": {"patients_with_conflict_burden": -1, "clinic_id": 1}},
    ]

    rows = await (
        database[CONFLICTS_COLLECTION].aggregate(pipeline).to_list(length=1000)
    )
    clinics = [ClinicConflictBurdenCount.model_validate(row) for row in rows]

    return ClinicConflictBurdenResponse(
        window_days=days,
        minimum_conflicts=minimum_conflicts,
        generated_at=utc_now(),
        total_clinics=len(clinics),
        clinics=clinics,
    )
