from dataclasses import dataclass

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel

PATIENTS_COLLECTION = "patients"
MEDICATION_SNAPSHOTS_COLLECTION = "medication_snapshots"
CONFLICTS_COLLECTION = "conflicts"


@dataclass(frozen=True)
class CollectionSchema:
    name: str
    indexes: tuple[IndexModel, ...]


PATIENTS_SCHEMA = CollectionSchema(
    name=PATIENTS_COLLECTION,
    indexes=(
        IndexModel([("patient_key", ASCENDING)], name="uq_patient_key", unique=True),
        IndexModel(
            [("identifiers.system", ASCENDING), ("identifiers.value", ASCENDING)],
            name="uq_identifier_system_value",
            unique=True,
            sparse=True,
        ),
        IndexModel([("updated_at", DESCENDING)], name="ix_patients_updated_at_desc"),
    ),
)

MEDICATION_SNAPSHOTS_SCHEMA = CollectionSchema(
    name=MEDICATION_SNAPSHOTS_COLLECTION,
    indexes=(
        IndexModel(
            [("patient_id", ASCENDING), ("snapshot_version", ASCENDING)],
            name="uq_patient_snapshot_version",
            unique=True,
        ),
        IndexModel(
            [("patient_id", ASCENDING), ("generated_at", DESCENDING)],
            name="ix_snapshots_patient_generated_desc",
        ),
        IndexModel(
            [("source_versions.source", ASCENDING), ("source_versions.version", ASCENDING)],
            name="ix_snapshots_source_version",
        ),
        IndexModel(
            [("merged_medications.rxnorm_code", ASCENDING)],
            name="ix_snapshots_rxnorm",
        ),
    ),
)

CONFLICTS_SCHEMA = CollectionSchema(
    name=CONFLICTS_COLLECTION,
    indexes=(
        IndexModel(
            [("snapshot_id", ASCENDING), ("conflict_key", ASCENDING)],
            name="uq_snapshot_conflict_key",
            unique=True,
        ),
        IndexModel(
            [("patient_id", ASCENDING), ("status", ASCENDING), ("detected_at", DESCENDING)],
            name="ix_conflicts_patient_status_detected",
        ),
        IndexModel(
            [("status", ASCENDING), ("detected_at", DESCENDING)],
            name="ix_conflicts_queue",
        ),
        IndexModel(
            [("medications.rxnorm_code", ASCENDING)],
            name="ix_conflicts_rxnorm",
        ),
    ),
)

COLLECTION_SCHEMAS = (
    PATIENTS_SCHEMA,
    MEDICATION_SNAPSHOTS_SCHEMA,
    CONFLICTS_SCHEMA,
)


async def ensure_indexes(database: AsyncIOMotorDatabase) -> None:
    for schema in COLLECTION_SCHEMAS:
        collection = database[schema.name]
        await collection.create_indexes(list(schema.indexes))
