from app.db.schema import (
    COLLECTION_SCHEMAS,
    CONFLICTS_COLLECTION,
    CONFLICTS_SCHEMA,
    MEDICATION_SNAPSHOTS_COLLECTION,
    MEDICATION_SNAPSHOTS_SCHEMA,
    PATIENTS_COLLECTION,
    PATIENTS_SCHEMA,
)


def _index_documents(schema_name: str):
    schema_lookup = {
        PATIENTS_COLLECTION: PATIENTS_SCHEMA,
        MEDICATION_SNAPSHOTS_COLLECTION: MEDICATION_SNAPSHOTS_SCHEMA,
        CONFLICTS_COLLECTION: CONFLICTS_SCHEMA,
    }
    schema = schema_lookup[schema_name]
    return {index.document["name"]: index.document for index in schema.indexes}


def test_collection_names_are_stable() -> None:
    assert [schema.name for schema in COLLECTION_SCHEMAS] == [
        PATIENTS_COLLECTION,
        MEDICATION_SNAPSHOTS_COLLECTION,
        CONFLICTS_COLLECTION,
    ]


def test_patients_schema_includes_unique_business_keys() -> None:
    index_docs = _index_documents(PATIENTS_COLLECTION)

    assert index_docs["uq_patient_key"]["unique"] is True
    assert list(index_docs["uq_patient_key"]["key"].items()) == [("patient_key", 1)]
    assert index_docs["uq_identifier_system_value"]["unique"] is True


def test_snapshot_schema_includes_multi_source_versioning_indexes() -> None:
    index_docs = _index_documents(MEDICATION_SNAPSHOTS_COLLECTION)

    assert index_docs["uq_patient_snapshot_version"]["unique"] is True
    assert list(index_docs["uq_patient_snapshot_version"]["key"].items()) == [
        ("patient_id", 1),
        ("snapshot_version", 1),
    ]
    assert "ix_snapshots_source_version" in index_docs


def test_conflicts_schema_supports_triage_queries() -> None:
    index_docs = _index_documents(CONFLICTS_COLLECTION)

    assert index_docs["uq_snapshot_conflict_key"]["unique"] is True
    assert "ix_conflicts_patient_status_detected" in index_docs
    assert "ix_conflicts_queue" in index_docs
