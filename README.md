# GeoCheck Next

Scalable customer and distribution-point (DP/FDP) location data-quality application for a telecommunications provider with an expected scale of 600,000 customer rows and 150,000 DPs.

## Architecture

- Frontend: React + TypeScript + Vite
- API: Python + FastAPI
- Database: MongoDB
- Processing: chunked, resumable background validation
- Core collections: `validation_runs`, `customer_records`, `dp_records`
- Reference data: versioned local GIS/LEA/provider-region files

## Safety and correctness rules

1. Preserve source rows; never silently discard duplicates or conflicting records.
2. Distinguish `PASS`, `FAIL`, `UNVERIFIED`, and `NOT_APPLICABLE`.
3. Do not treat customer errors alone as proof that a DP is wrong.
4. Do not store an unbounded customer array in a DP document.
5. Process in bounded batches and paginate large result sets.
6. Record the rules and reference-data versions used by each run.
7. Do not invent the service provider's regions or LEA mapping.
8. Do not report production readiness until scale and reconciliation tests pass.

## Run locally

### Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# Copy .env.example to .env and configure MONGO_URI
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api` calls to `http://127.0.0.1:8000` during development.

## Phase 0 inputs still required

- Sanitized sample of the actual CSV with real headers (remove names, phone numbers and precise customer coordinates; preserve data types and issue patterns).
- Provider's official operational-region mapping.
- Approved telephone-area/LEA reference table.
- Meaning/units of line and overhead distance columns.
- Agreed DP association thresholds and duplicate-ID policy.

See `docs/PHASE_0_DATA_CONTRACT.md` and `docs/VALIDATION_RULES.md`.
