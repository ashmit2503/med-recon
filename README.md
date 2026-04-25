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

## Commit Policy

- Use conventional commit messages.
- Keep each logical unit of work in its own atomic commit.
- Do not squash unrelated changes into a single commit.
