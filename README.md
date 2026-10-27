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

## Quick start with Docker Desktop (recommended)

1. Install/start Docker Desktop.
2. Copy the example environment file and generate a private key. In PowerShell, from the repository root:

~~~powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
~~~

3. Open the new root .env file, replace GEOCHECK_API_KEY with the generated value, and save it.
4. Start the whole stack:

~~~powershell
docker compose up --build
~~~

5. Open **http://localhost:5173** in your browser. Paste the same GEOCHECK_API_KEY value into the API access key field, then upload a synthetic CSV first.

The Compose stack starts MongoDB, the FastAPI API, the background worker and the frontend. MongoDB data and temporary uploads persist in Docker-managed volumes. Stop the stack with Ctrl+C, then use `docker compose down`. The database volume is retained. **Do not run `docker compose down -v` unless you intentionally want to delete the local MongoDB database and upload volume.**

The shared-key login is for a local development workspace, not an Internet-facing security model. Keep the ports local and do not upload real customer data until the organization approves access control, data retention and the reference/map data sources.

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

The upload endpoint accepts CSV files only. Default limits are 512 MiB and 1,000,000 source rows; override them via MAX_UPLOAD_BYTES and MAX_CSV_ROWS. The frontend calls the protected /api/validation-runs/preview endpoint to read the header and propose mappings for the required customer audit fields: customer ID, router/service number, customer coordinates, address, DP ID and coordinates, and line distance. The worker performs a full syntax/field-size/row-count preflight before writing validation records. The API and worker must share the same upload directory and MongoDB database.

Customer-to-DP distance defaults to the local Haversine straight-line calculation, with a 500 m threshold. To use Google Maps road-route distance instead, set `DISTANCE_METHOD=google_routes` and configure a backend-only `GOOGLE_MAPS_SERVER_KEY` after enabling the Routes API in your Google Cloud project. To geocode customer addresses, set `GOOGLE_GEOCODING_ENABLED=true` and enable the Geocoding API for that key. Both integrations have a per-run request cap and return `UNVERIFIED` rather than a guessed result when the provider does not return a usable response. Google road-route distance is not actual overhead cable length. See `docs/CUSTOMER_DP_AUDIT.md` and `docs/VALIDATION_RULES.md` for details.

The API key is a basic shared-secret gate for development, not full identity-level authentication. Do not expose these endpoints publicly or upload real customer data until identity/access controls, transport security, storage security, retention/deletion rules, and operational monitoring have been reviewed.

### Frontend

~~~bash
cd frontend
npm install
npm run dev
~~~

Vite proxies /api calls to http://127.0.0.1:8000 during development. The frontend includes API-key connection, CSV header preview and column mapping, upload/run progress, paginated customer/DP results, issue explorer, reported-geography hierarchy, map explorer, and CSV exports. It uses VITE_MAP_TILE_URL when configured, otherwise it displays OpenStreetMap tiles; tile requests share the map viewport with the selected tile provider, so use synthetic data until an approved provider/tile service is configured.

### Benchmark the streaming pipeline

The benchmark requires a disposable MongoDB instance and deliberately refuses to fall back to the application MONGO_URI. It creates a random database name and drops that database at the end unless --keep-db is explicitly supplied. Run this only against a test MongoDB service:

~~~powershell
$env:GEOCHECK_BENCH_MONGO_URI = "mongodb://127.0.0.1:27017"
python backend/scripts/benchmark_validation.py --rows 10000 --dp-count 2500 --batch-size 1000
~~~

Run the full scale target after the smaller benchmark succeeds and you have checked disk space and database capacity:

~~~powershell
python backend/scripts/benchmark_validation.py --rows 600000 --dp-count 150000 --batch-size 1000 --report test-data/generated/geocheck-benchmark-report.json
~~~

The report includes generated/stored row reconciliation, elapsed generation/validation time, throughput and peak Python heap allocation. Peak Python heap is not total process or MongoDB memory; record host RAM, disk, MongoDB configuration and index storage separately. Synthetic rows and generated reports are local test artifacts, not production customer data.

### Backend unit tests

From the repository root:

~~~powershell
python -m unittest discover -s backend/tests -p "test_*.py" -v
~~~

The same test command runs automatically in GitHub Actions for pushes to main and feature branches and for pull requests targeting main. Most unit/API tests use synthetic fixtures and in-memory collection fakes. A separate CI workflow starts a disposable MongoDB service and exercises atomic queue claims, clean replay, and a small benchmark smoke run. The full 600,000-row benchmark is intentionally not run on every CI build; run it on the machine/environment you plan to use.

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
