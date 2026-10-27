# GeoCheck Next

Scalable customer and distribution-point (DP/FDP) location data-quality application for a telecommunications provider with an expected scale of 600,000 customer rows and 150,000 DPs.

## Architecture

- Frontend: React + TypeScript + Vite
- API: Python + FastAPI
- Database: MongoDB
- Processing: streaming CSV upload, MongoDB-backed queue and separate bounded-batch worker
- Core collections: validation_runs, customer_records, dp_records
- Reference data: versioned local GIS/LEA/provider-region files

## Safety and correctness rules

1. Preserve source rows; never silently discard duplicates or conflicting records.
2. Distinguish PASS, FAIL, UNVERIFIED, and NOT_APPLICABLE.
3. Do not treat customer errors alone as proof that a DP is wrong.
4. Do not store an unbounded customer array in a DP document.
5. Process in bounded batches and paginate large result sets.
6. Record the rules and reference-data versions used by each run.
7. Do not invent the service provider's regions or LEA mapping.
8. Do not report production readiness until scale and reconciliation tests pass.

## Run locally

### Backend API

~~~bash
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# Copy .env.example to .env, then edit it and set a unique GEOCHECK_API_KEY.
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
~~~

The API uses MongoDB and creates its indexes during startup. /api/health checks database connectivity. CSV upload, run-status, results, and reference-inventory endpoints fail closed until GEOCHECK_API_KEY is configured.

### Background worker

Open a second terminal and run this from the backend directory, using the same .env file:

~~~bash
python -m app.worker
~~~

The worker claims queued runs atomically from MongoDB, preflights the complete CSV, loads the configured reference files, and processes rows in bounded batches. It records reference file hashes with each run. Run only one worker process for this MVP. The API and worker must share the same persistent UPLOAD_DIR; in containers, mount the same persistent volume at that location in both services.

### Upload and check a run

After setting GEOCHECK_API_KEY to your own secret in backend/.env, submit a CSV:

~~~bash
curl.exe -H "X-API-Key: YOUR_SECRET" -F "file=@C:\\path\\customers.csv" http://127.0.0.1:8000/api/validation-runs
~~~

The response contains a run_id and poll_url. Check progress:

~~~bash
curl.exe -H "X-API-Key: YOUR_SECRET" http://127.0.0.1:8000/api/validation-runs/YOUR_RUN_ID
~~~

The upload endpoint accepts CSV files only. Default limits are 512 MiB and 1,000,000 source rows; override them via MAX_UPLOAD_BYTES and MAX_CSV_ROWS. Automatic column detection requires customer latitude/longitude and DP latitude/longitude. To supply a custom mapping, pass column_mapping as a JSON form field containing logical-field-to-header mappings.

The API key is a basic shared-secret gate for development, not full identity-level authentication. Do not expose these endpoints publicly or upload real customer data until identity/access controls, transport security, storage security, retention/deletion rules, and operational monitoring have been reviewed.

### Frontend

~~~bash
cd frontend
npm install
npm run dev
~~~

Vite proxies /api calls to http://127.0.0.1:8000 during development. The current frontend remains a scaffold; it does not yet provide the complete upload, mapping, hierarchy, map, and export experience.

### Backend unit tests

From the repository root:

~~~powershell
python -m unittest discover -s backend/tests -p "test_*.py" -v
~~~

The same test command runs automatically in GitHub Actions for pushes to main and feature branches and for pull requests targeting main. Tests use synthetic fixtures and in-memory collection fakes; they are not a live MongoDB integration or performance test.

## Queue and failure behavior

The queue is persisted in validation_runs, so queued work survives API and worker process restarts as long as MongoDB and the shared upload volume persist. The worker preflights CSV syntax and row/field limits before processing. A run that stops reporting progress for WORKER_STALE_AFTER_SECONDS (default 900 seconds) is requeued for one clean replay when attempts remain: the worker re-runs preflight, deletes that run's partial customer/DP outputs, resets counters, and starts again from the source file. After WORKER_MAX_ATTEMPTS is reached, the run is marked failed, partial records remain attached to the failed run for diagnosis, and the stored upload is removed. This restart behavior assumes one worker process for the MVP; multi-worker leases and production-grade coordination are not implemented.

## Generate synthetic test data

From the repository root, generate a small 50-row fixture:

~~~powershell
python test-data/generate_synthetic_csv.py
~~~

Generate a scale fixture for the planned load test:

~~~powershell
python test-data/generate_synthetic_csv.py --rows 600000 --dp-count 150000
~~~

Generated files are written to test-data/generated/ and ignored by Git because scale fixtures may be large. The coordinates, addresses and identifiers are synthetic. They must never be treated as a trusted Sri Lankan boundary, town, LEA or provider-region reference.

## Phase 0 inputs still required

- Sanitized sample of the actual CSV with real headers (remove names, phone numbers and precise customer coordinates; preserve data types and issue patterns).
- Provider's official operational-region mapping.
- Approved telephone-area/LEA reference table.
- Approved tolerance for overhead_distance_m comparison (its meaning is confirmed as straight-line customer-to-DP distance; the current 25 m tolerance is provisional).
- Agreed DP association thresholds and duplicate-ID policy.

See docs/PHASE_0_DATA_CONTRACT.md, docs/VALIDATION_RULES.md, docs/BATCH_PROCESSING_DESIGN.md, docs/UPLOAD_AND_WORKER.md and docs/REFERENCE_DATA.md. The map explorer uses the VITE_MAP_TILE_URL variable; the default OpenStreetMap tiles should be used with synthetic data unless external tile access is approved.
