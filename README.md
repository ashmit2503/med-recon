# Medication Reconciliation & Conflict Reporting Service

FastAPI service scaffold for medication reconciliation and conflict reporting workflows, with MongoDB-ready infrastructure.

## Stack

- Python 3.12
- FastAPI
- MongoDB (Motor async driver)
- pip requirements workflow

## Project Structure

```
app/
  api/
  core/
  db/
  models/
  schemas/
  services/
tests/
```

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

3. Copy environment variables:

```bash
copy .env.example .env
```

4. Run the API:

```bash
uvicorn app.main:app --reload
```

5. Verify health check:

```bash
GET http://127.0.0.1:8000/api/health
```

## Seed Synthetic Data

Generate realistic synthetic data across EHR, pharmacy dispense, and insurance claims
sources, including clean and intentionally conflicted patients:

```bash
python -m app.scripts.seed_synthetic_data --patients 15
```

Notes:

- Patient count must be between 10 and 20.
- Re-running the script replaces prior seeded records with `seed-patient-*` keys.
- For generation checks without database writes:

```bash
python -m app.scripts.seed_synthetic_data --patients 15 --dry-run
```

## Domain Schema (MongoDB + Pydantic)

Domain models are implemented in:

- `app/models/patient.py`
- `app/models/medication_snapshot.py`
- `app/models/conflict.py`
- `app/db/schema.py`

### patients collection

Stores patient identity, demographics, and stable identifiers used for matching.

```json
{
  "_id": "uuid",
  "patient_key": "patient-001",
  "status": "active",
  "demographics": {
    "given_name": "Avery",
    "family_name": "Nguyen",
    "date_of_birth": "1985-04-02",
    "sex": "female"
  },
  "identifiers": [
    {"system": "MRN", "value": "12345"},
    {"system": "NATIONAL_ID", "value": "987654321"}
  ],
  "allergies": ["penicillin"],
  "active_snapshot_id": "snapshot-003",
  "created_at": "2026-04-25T10:20:00Z",
  "updated_at": "2026-04-25T10:20:00Z"
}
```

### medication_snapshots collection (multi-source versioning)

Captures reconciliation snapshots per patient. Each snapshot stores:

- A monotonic `snapshot_version` per patient.
- `source_versions[]` with one version entry per source (EHR, claims, pharmacy, patient-reported).
- `merged_medications[]` as the reconciled view used by downstream conflict detection.

```json
{
  "_id": "uuid",
  "patient_id": "patient-001",
  "snapshot_version": 3,
  "source_versions": [
    {
      "source": "ehr",
      "version": "ehr-v23",
      "captured_at": "2026-04-25T09:58:00Z",
      "medications": [
        {"medication_id": "med-1", "name": "Atorvastatin", "rxnorm_code": "83367"}
      ]
    },
    {
      "source": "pharmacy_dispense",
      "version": "rx-v8",
      "captured_at": "2026-04-25T10:00:00Z",
      "medications": [
        {"medication_id": "med-2", "name": "Lisinopril", "rxnorm_code": "29046"}
      ]
    }
  ],
  "merged_medications": [
    {"medication_id": "med-1", "name": "Atorvastatin", "rxnorm_code": "83367"},
    {"medication_id": "med-2", "name": "Lisinopril", "rxnorm_code": "29046"}
  ],
  "generated_at": "2026-04-25T10:01:00Z",
  "created_at": "2026-04-25T10:01:00Z",
  "updated_at": "2026-04-25T10:01:00Z"
}
```

Validation rule: one source entry per snapshot (`source_versions` cannot contain duplicate source names).

### conflicts collection

Stores detected medication conflicts tied to a snapshot.

```json
{
  "_id": "uuid",
  "patient_id": "patient-001",
  "snapshot_id": "snapshot-003",
  "conflict_key": "snapshot-003:drug_interaction:med-1-med-2",
  "conflict_type": "drug_interaction",
  "severity": "high",
  "status": "open",
  "title": "Potential interaction detected",
  "description": "Atorvastatin and clarithromycin overlap.",
  "medications": [
    {"medication_id": "med-1", "name": "Atorvastatin", "rxnorm_code": "83367"},
    {"medication_id": "med-2", "name": "Clarithromycin", "rxnorm_code": "18631"}
  ],
  "detected_at": "2026-04-25T10:02:00Z",
  "created_at": "2026-04-25T10:02:00Z",
  "updated_at": "2026-04-25T10:02:00Z"
}
```

Validation rule: closed conflicts (`resolved` or `dismissed`) must include `resolved_at`.

## Indexing Rationale

Index definitions live in `app/db/schema.py` and are applied on Mongo connect.

- `patients.uq_patient_key` (unique): guarantees one canonical domain patient key.
- `patients.uq_identifier_system_value` (unique, sparse): prevents duplicate identifier tuples across patients while allowing missing optional identifiers.
- `patients.ix_patients_updated_at_desc`: supports recency queries and sync jobs.

- `medication_snapshots.uq_patient_snapshot_version` (unique): enforces one snapshot version per patient.
- `medication_snapshots.ix_snapshots_patient_generated_desc`: supports latest-snapshot lookup by patient.
- `medication_snapshots.ix_snapshots_source_version`: supports lineage/audit queries by source and source version.
- `medication_snapshots.ix_snapshots_rxnorm`: supports medication-level lookup and analytics.

- `conflicts.uq_snapshot_conflict_key` (unique): deduplicates conflicts for a given snapshot.
- `conflicts.ix_conflicts_patient_status_detected`: supports patient triage views (open/acknowledged first).
- `conflicts.ix_conflicts_queue`: supports global conflict work queues.
- `conflicts.ix_conflicts_rxnorm`: supports medication-centric conflict analytics.

## Denormalization Trade-Offs

- `merged_medications` duplicates data from `source_versions[].medications`.
  - Benefit: read-optimized conflict detection and clinician-facing reconciliation views.
  - Cost: write amplification and risk of divergence if merge logic is incorrect.

- `conflicts.medications[]` duplicates key medication descriptors from snapshot records.
  - Benefit: conflict documents remain self-contained for queue processing and reporting.
  - Cost: updates to medication labels/codings are not automatically back-propagated.

- `patients.active_snapshot_id` caches latest snapshot reference.
  - Benefit: O(1) lookup for current reconciliation context.
  - Cost: requires transactional discipline (or compensating updates) when new snapshots are published.

## Commit Policy

- Use conventional commit messages.
- Keep each logical unit of work in its own atomic commit.
- Do not squash unrelated changes into a single commit.
