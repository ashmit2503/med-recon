from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import date
import random
import re
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from app.core.config import get_settings
from app.db.schema import (
    CONFLICTS_COLLECTION,
    MEDICATION_SNAPSHOTS_COLLECTION,
    PATIENTS_COLLECTION,
    ensure_indexes,
)
from app.models.conflict import ConflictType
from app.models.medication_snapshot import (
    MedicationItem,
    MedicationSnapshotDocument,
    MedicationSource,
    MedicationStatus,
    SourceMedicationVersion,
)
from app.models.patient import PatientDocument, PatientSex
from app.services import ConflictDetectionEngine

SCENARIOS = (
    "clean",
    "dose_mismatch",
    "dangerous_combo",
    "status_disagreement",
    "mixed",
)

CLINICS = (
    "clinic-north",
    "clinic-east",
    "clinic-south",
    "clinic-west",
)

FIRST_NAMES = (
    "Avery",
    "Jordan",
    "Taylor",
    "Priya",
    "Noah",
    "Leah",
    "Mason",
    "Sophia",
    "Diego",
    "Mina",
    "Harper",
    "Elijah",
    "Nora",
    "Camila",
    "Ibrahim",
    "Grace",
)

LAST_NAMES = (
    "Nguyen",
    "Patel",
    "Garcia",
    "Wilson",
    "Kim",
    "Singh",
    "Ali",
    "Martinez",
    "Brown",
    "Taylor",
    "Carter",
    "Anderson",
    "Thomas",
    "Hernandez",
    "Scott",
    "Moore",
)


@dataclass(frozen=True)
class SeedStats:
    patient_count: int
    clean_patients: int
    conflicted_patients: int
    conflict_type_counts: dict[str, int]
    scenario_counts: dict[str, int]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Seed MongoDB with synthetic multi-source medication data, including "
            "intentional and clean conflict patterns."
        )
    )
    parser.add_argument(
        "--patients",
        type=int,
        default=15,
        help="Number of synthetic patients to seed (must be between 10 and 20).",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Deterministic random seed used to generate synthetic demographics.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate synthetic records without writing to MongoDB.",
    )
    return parser.parse_args()


def _validate_patient_count(count: int) -> None:
    if count < 10 or count > 20:
        raise ValueError("--patients must be between 10 and 20.")


async def _delete_existing_seed_data(database: AsyncIOMotorDatabase) -> int:
    patients_collection = database[PATIENTS_COLLECTION]
    snapshot_collection = database[MEDICATION_SNAPSHOTS_COLLECTION]
    conflicts_collection = database[CONFLICTS_COLLECTION]

    existing_seed_patients = await patients_collection.find(
        {"patient_key": {"$regex": r"^seed-patient-"}}
    ).to_list(length=5000)
    if not existing_seed_patients:
        return 0

    patient_ids = [str(document["_id"]) for document in existing_seed_patients]
    await conflicts_collection.delete_many({"patient_id": {"$in": patient_ids}})
    await snapshot_collection.delete_many({"patient_id": {"$in": patient_ids}})
    await patients_collection.delete_many({"_id": {"$in": patient_ids}})
    return len(patient_ids)


def _build_patient_scenario(index: int) -> str:
    return SCENARIOS[index % len(SCENARIOS)]


def _build_medication_payloads(
    scenario: str,
) -> dict[MedicationSource, list[dict[str, Any]]]:
    clean_payload = {
        MedicationSource.EHR: [
            {
                "name": "Metformin",
                "dose": "500 mg",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "6809",
            },
            {
                "name": "Atorvastatin",
                "dose": "20 mg",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "83367",
            },
            {
                "name": "Lisinopril",
                "dose": "10 mg",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "29046",
            },
        ],
        MedicationSource.PHARMACY_DISPENSE: [
            {
                "name": "Glucophage",
                "dose": "0.5 g",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "6809",
            },
            {
                "name": "Atorvastatin 20 mg tablet",
                "dose": "20 mg",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "83367",
            },
            {
                "name": "Lisinopril",
                "dose": "10 mg",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "29046",
            },
        ],
        MedicationSource.INSURANCE_CLAIMS: [
            {
                "name": "metformin 500mg tablet",
                "dose": "500mg",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "6809",
            },
            {
                "name": "Atorvastatin",
                "dose": "20 mg",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "83367",
            },
            {
                "name": "Lisinopril",
                "dose": "0.01 g",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "29046",
            },
        ],
    }

    if scenario == "clean":
        return clean_payload

    payload = {
        source: [dict(item) for item in items]
        for source, items in clean_payload.items()
    }

    if scenario in {"dose_mismatch", "mixed"}:
        payload[MedicationSource.EHR].append(
            {
                "name": "Warfarin",
                "dose": "5 mg",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "855332",
            }
        )
        payload[MedicationSource.PHARMACY_DISPENSE].append(
            {
                "name": "Warfarin",
                "dose": "10 mg" if scenario == "mixed" else "7.5 mg",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "855332",
            }
        )
        payload[MedicationSource.INSURANCE_CLAIMS].append(
            {
                "name": "Warfarin",
                "dose": "5 mg",
                "status": (
                    MedicationStatus.DISCONTINUED
                    if scenario == "mixed"
                    else MedicationStatus.ACTIVE
                ),
                "rxnorm_code": "855332",
            }
        )

    if scenario == "dangerous_combo":
        payload[MedicationSource.EHR].append(
            {
                "name": "Warfarin",
                "dose": "5 mg",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "855332",
            }
        )

    if scenario in {"dangerous_combo", "mixed"}:
        payload[MedicationSource.PHARMACY_DISPENSE].append(
            {
                "name": "Advil 200 mg tablet",
                "dose": "400 mg",
                "status": MedicationStatus.ACTIVE,
                "rxnorm_code": "5640",
            }
        )

    if scenario == "status_disagreement":
        for source in (MedicationSource.EHR, MedicationSource.PHARMACY_DISPENSE):
            payload[source].append(
                {
                    "name": "Losartan",
                    "dose": "50 mg",
                    "status": MedicationStatus.ACTIVE,
                    "rxnorm_code": "979468",
                }
            )
        payload[MedicationSource.INSURANCE_CLAIMS].append(
            {
                "name": "Losartan",
                "dose": "50 mg",
                "status": MedicationStatus.DISCONTINUED,
                "rxnorm_code": "979468",
            }
        )

    return payload


def _build_medication_id(
    source: MedicationSource,
    patient_index: int,
    medication_name: str,
    sequence: int,
) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", medication_name.lower()).strip("-")
    return f"seed-{patient_index + 1}-{source.value}-{sequence}-{slug}"[:80]


def _build_source_versions(
    patient_index: int,
    scenario: str,
) -> list[SourceMedicationVersion]:
    payloads = _build_medication_payloads(scenario)
    source_versions: list[SourceMedicationVersion] = []

    for source in (
        MedicationSource.EHR,
        MedicationSource.PHARMACY_DISPENSE,
        MedicationSource.INSURANCE_CLAIMS,
    ):
        medications: list[MedicationItem] = []
        for index, payload in enumerate(payloads[source], start=1):
            medications.append(
                MedicationItem(
                    medication_id=_build_medication_id(
                        source,
                        patient_index,
                        str(payload["name"]),
                        index,
                    ),
                    name=str(payload["name"]),
                    dose=str(payload["dose"]),
                    status=payload["status"],
                    rxnorm_code=str(payload["rxnorm_code"]),
                    frequency="daily",
                )
            )

        source_versions.append(
            SourceMedicationVersion(
                source=source,
                version=f"seed-{scenario}-{patient_index + 1}-{source.value}",
                medications=medications,
            )
        )

    return source_versions


def _merge_medications(source_versions: list[SourceMedicationVersion]) -> list[MedicationItem]:
    merged: dict[str, MedicationItem] = {}
    for source_version in source_versions:
        for medication in source_version.medications:
            if medication.rxnorm_code:
                key = f"rxnorm:{medication.rxnorm_code.lower()}"
            else:
                key = f"name:{medication.name.lower()}"

            existing = merged.get(key)
            if existing is None:
                merged[key] = medication
                continue

            if existing.status != MedicationStatus.ACTIVE and medication.status == MedicationStatus.ACTIVE:
                merged[key] = medication
                continue

            if existing.dose is None and medication.dose is not None:
                merged[key] = medication

    return sorted(merged.values(), key=lambda medication: medication.name.lower())


async def _seed_patients(
    database: AsyncIOMotorDatabase,
    patient_count: int,
    random_seed: int,
) -> SeedStats:
    randomizer = random.Random(random_seed)
    await ensure_indexes(database)

    removed_records = await _delete_existing_seed_data(database)

    engine = ConflictDetectionEngine()
    patients_collection = database[PATIENTS_COLLECTION]
    snapshots_collection = database[MEDICATION_SNAPSHOTS_COLLECTION]
    conflicts_collection = database[CONFLICTS_COLLECTION]

    conflict_type_counter: Counter[str] = Counter()
    scenario_counter: Counter[str] = Counter()
    clean_patients = 0

    for index in range(patient_count):
        scenario = _build_patient_scenario(index)
        scenario_counter[scenario] += 1

        first_name = randomizer.choice(FIRST_NAMES)
        last_name = randomizer.choice(LAST_NAMES)
        clinic_id = randomizer.choice(CLINICS)

        birth_year = randomizer.randint(1948, 2002)
        birth_month = randomizer.randint(1, 12)
        birth_day = randomizer.randint(1, 28)

        patient = PatientDocument(
            _id=f"seed-patient-id-{index + 1:03d}",
            patient_key=f"seed-patient-{index + 1:03d}",
            demographics={
                "given_name": first_name,
                "family_name": last_name,
                "date_of_birth": date(birth_year, birth_month, birth_day),
                "sex": randomizer.choice(list(PatientSex)),
            },
            identifiers=[
                {"system": "MRN", "value": f"SEED-MRN-{100000 + index}"},
                {"system": "NATIONAL_ID", "value": f"SEED-NID-{900000 + index}"},
            ],
            allergies=randomizer.choice(
                [
                    [],
                    ["penicillin"],
                    ["shellfish"],
                    ["latex", "sulfa"],
                ]
            ),
            metadata={
                "clinic_id": clinic_id,
                "seed_scenario": scenario,
            },
        )

        source_versions = _build_source_versions(index, scenario)
        snapshot = MedicationSnapshotDocument(
            _id=f"{patient.id}:v1",
            patient_id=patient.id,
            snapshot_version=1,
            source_versions=source_versions,
            merged_medications=_merge_medications(source_versions),
            metadata={
                "seeded": "true",
                "seed_scenario": scenario,
            },
        )

        detected_conflicts = engine.detect_conflicts(snapshot)
        if not detected_conflicts:
            clean_patients += 1

        for conflict in detected_conflicts:
            conflict_type_counter[conflict.conflict_type.value] += 1

        patient.active_snapshot_id = snapshot.id
        patient.touch()

        await patients_collection.insert_one(patient.model_dump(by_alias=True))
        await snapshots_collection.insert_one(snapshot.model_dump(by_alias=True))

        for conflict in detected_conflicts:
            conflict.metadata["seed_scenario"] = scenario
            conflict.metadata["seeded"] = "true"
            conflict.touch()
            await conflicts_collection.update_one(
                {
                    "snapshot_id": conflict.snapshot_id,
                    "conflict_key": conflict.conflict_key,
                },
                {"$setOnInsert": conflict.model_dump(by_alias=True)},
                upsert=True,
            )

    conflicted_patients = patient_count - clean_patients

    print(f"Removed existing seeded patients: {removed_records}")
    print(f"Seeded synthetic patients: {patient_count}")
    print(f"Clean patients (no conflicts): {clean_patients}")
    print(f"Patients with conflicts: {conflicted_patients}")
    print("Scenario distribution:")
    for scenario, count in sorted(scenario_counter.items()):
        print(f"  - {scenario}: {count}")
    print("Conflict distribution:")
    if conflict_type_counter:
        for conflict_type, count in sorted(conflict_type_counter.items()):
            print(f"  - {conflict_type}: {count}")
    else:
        print("  - none")

    return SeedStats(
        patient_count=patient_count,
        clean_patients=clean_patients,
        conflicted_patients=conflicted_patients,
        conflict_type_counts=dict(conflict_type_counter),
        scenario_counts=dict(scenario_counter),
    )


def _validate_seed_stats(stats: SeedStats) -> None:
    if stats.clean_patients <= 0:
        raise RuntimeError("Seeding did not produce any clean patients without conflicts.")

    if stats.conflicted_patients <= 0:
        raise RuntimeError("Seeding did not produce any patients with conflicts.")

    required_conflicts = {
        ConflictType.DOSAGE_MISMATCH.value,
        ConflictType.DRUG_INTERACTION.value,
        ConflictType.SOURCE_DISAGREEMENT.value,
    }
    missing = required_conflicts - set(stats.conflict_type_counts)
    if missing:
        raise RuntimeError(
            "Seeding did not produce all required conflict categories: "
            + ", ".join(sorted(missing))
        )


async def _dry_run(patient_count: int, random_seed: int) -> SeedStats:
    engine = ConflictDetectionEngine()
    randomizer = random.Random(random_seed)
    scenario_counter: Counter[str] = Counter()
    conflict_type_counter: Counter[str] = Counter()
    clean_patients = 0

    for index in range(patient_count):
        scenario = _build_patient_scenario(index)
        scenario_counter[scenario] += 1

        first_name = randomizer.choice(FIRST_NAMES)
        last_name = randomizer.choice(LAST_NAMES)
        clinic_id = randomizer.choice(CLINICS)

        patient = PatientDocument(
            _id=f"seed-patient-id-{index + 1:03d}",
            patient_key=f"seed-patient-{index + 1:03d}",
            demographics={
                "given_name": first_name,
                "family_name": last_name,
                "date_of_birth": date(1990, 1, 1),
                "sex": randomizer.choice(list(PatientSex)),
            },
            identifiers=[
                {"system": "MRN", "value": f"SEED-MRN-{100000 + index}"},
            ],
            metadata={"clinic_id": clinic_id},
        )

        source_versions = _build_source_versions(index, scenario)
        snapshot = MedicationSnapshotDocument(
            _id=f"{patient.id}:v1",
            patient_id=patient.id,
            snapshot_version=1,
            source_versions=source_versions,
            merged_medications=_merge_medications(source_versions),
        )

        detected_conflicts = engine.detect_conflicts(snapshot)
        if not detected_conflicts:
            clean_patients += 1

        for conflict in detected_conflicts:
            conflict_type_counter[conflict.conflict_type.value] += 1

    stats = SeedStats(
        patient_count=patient_count,
        clean_patients=clean_patients,
        conflicted_patients=patient_count - clean_patients,
        conflict_type_counts=dict(conflict_type_counter),
        scenario_counts=dict(scenario_counter),
    )

    print(f"Dry-run synthetic patients generated: {patient_count}")
    print(f"Dry-run clean patients: {stats.clean_patients}")
    print(f"Dry-run patients with conflicts: {stats.conflicted_patients}")
    for conflict_type, count in sorted(stats.conflict_type_counts.items()):
        print(f"  - {conflict_type}: {count}")

    return stats


async def _run_async() -> None:
    args = _parse_args()
    _validate_patient_count(args.patients)

    if args.dry_run:
        stats = await _dry_run(args.patients, args.random_seed)
        _validate_seed_stats(stats)
        return

    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongodb_uri)

    try:
        database = client[settings.mongodb_database]
        try:
            stats = await _seed_patients(database, args.patients, args.random_seed)
            _validate_seed_stats(stats)
        except PyMongoError as error:
            raise RuntimeError(
                "MongoDB operation failed during seeding. "
                "Verify connection settings and database availability."
            ) from error
    finally:
        client.close()


def main() -> None:
    try:
        asyncio.run(_run_async())
    except (ValueError, RuntimeError) as error:
        print(f"Seed failed: {error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
