# Medication Reconciliation & Conflict Reporting Service

FastAPI service for ingesting medication lists from multiple sources, normalizing data,
detecting cross-source conflicts, and exposing conflict analytics.

## 5-Minute Quickstart

### Prerequisites

- Git
- Python 3.11+
- MongoDB running locally (default `mongodb://localhost:27017`)

### Clone, install, and run

```bash
git clone <your-repo-url>
cd med-recon
python -m venv .venv
```

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Smoke check:

- Open `http://127.0.0.1:8000/docs`
- Call `GET http://127.0.0.1:8000/api/health`

## One-Command Seed Data

Create realistic synthetic data (10-20 patients) across EHR, pharmacy, and claims with
both clean and intentionally conflicted cohorts:

```bash
python -m app.scripts.seed_synthetic_data --patients 15
```

Optional dry-run (no database writes):

```bash
python -m app.scripts.seed_synthetic_data --patients 15 --dry-run
```

## Architecture Overview

### High-level flow

1. Client ingests source medication list for a patient.
2. Service normalizes drug names, doses, and units.
3. Service creates a new patient snapshot version with all known sources.
4. Conflict engine detects dose mismatches, dangerous class combinations, and
   stopped-vs-active disagreements.
5. Conflicts are upserted and exposed via patient and analytics endpoints.

### Code layout

```
app/
  api/          # FastAPI routers and endpoint orchestration
  core/         # Settings/config
  db/           # Mongo connection + index setup
  models/       # Pydantic domain models (documents)
  schemas/      # API request/response schemas
  services/     # Domain logic: normalization, detection, registries
  scripts/      # Operational scripts (seed data)
tests/
```

### Main API surface

- `POST /api/patients/{patient_key}/sources/{source}/medications/ingest`
- `PATCH /api/patients/{patient_key}/conflicts/{conflict_id}/resolution`
- `GET /api/patients/{patient_key}/conflicts`
- `GET /api/analytics/clinics/{clinic_id}/patients-with-unresolved-conflicts`
- `GET /api/analytics/clinics/conflicts/high-burden`

## Schema Summary

### `patients`

- Identity and demographics
- Business key: `patient_key` (unique)
- Important fields:
  - `identifiers[]`
  - `metadata.clinic_id`
  - `active_snapshot_id`

### `medication_snapshots`

- Versioned patient medication state (`snapshot_version`)
- `source_versions[]` contains source-specific medication lists
- `merged_medications[]` stores reconciled, read-optimized view

### `conflicts`

- Conflict records tied to snapshot + patient
- Types currently detected:
  - `dosage_mismatch`
  - `drug_interaction`
  - `source_disagreement`
- Resolution fields:
  - `status`
  - `resolved_at`
  - `resolution_notes`

## Indexing Summary

Defined in `app/db/schema.py` and created on app startup.

- `patients`
  - unique: `patient_key`
  - unique sparse: `identifiers.system + identifiers.value`
  - recency: `updated_at`
- `medication_snapshots`
  - unique: `patient_id + snapshot_version`
  - latest snapshot lookup: `patient_id + generated_at`
  - source lineage: `source_versions.source + source_versions.version`
- `conflicts`
  - unique: `snapshot_id + conflict_key`
  - triage/work queue indexes on `status` and `detected_at`
  - medication analytics via `medications.rxnorm_code`

## Assumptions and Trade-offs

### Assumptions

- Patient identity is resolved upstream and represented by `patient_key`.
- Ingest creates a new snapshot version rather than mutating prior snapshots.
- Conflict status lifecycle is managed through explicit resolve/dismiss actions.

### Trade-offs

- Denormalization in snapshots (`merged_medications`) and conflicts (`medications[]`) improves
  read performance and analytics simplicity, at the cost of duplicated fields.
- Application-level upsert logic for conflicts avoids duplicate records but requires careful
  key construction and consistent conflict hashing.
- Scripted synthetic data favors deterministic scenario coverage over clinical completeness.

## Known Limitations

- No authentication/authorization layer yet.
- No pagination token strategy beyond simple `limit` parameters.
- No background job queue for heavy ingest/analysis workloads.
- No migration framework or environment-specific deployment manifests yet.
- Conflict logic is rule-based and static; no probabilistic clinical scoring.

## What I Would Do Next

1. Add authn/authz (JWT + role-based access for pharmacists/providers).
2. Add OpenTelemetry tracing and structured logging correlation IDs.
3. Add Mongo transactions where multi-collection writes need stronger guarantees.
4. Add load tests and performance budgets for ingest and analytics paths.
5. Add CI pipeline gates for lint, tests, and type checks.
6. Add Docker compose profile for one-command local stack startup.

## AI Usage

AI assistance was used to accelerate scaffolding and implementation drafts, including:

- API/router boilerplate
- Model and schema shaping
- test skeleton generation
- README restructuring

All AI-produced code and docs were reviewed, refined, and validated by running lint/tests
and enforcing repository commit standards (atomic conventional commits).

## Developer Quality Checks

```bash
python -m pytest -q
python -m ruff check .
```

## Commit Policy

- Use conventional commit messages.
- Keep each logical unit of work in its own atomic commit.
- Do not squash unrelated changes into a single commit.
