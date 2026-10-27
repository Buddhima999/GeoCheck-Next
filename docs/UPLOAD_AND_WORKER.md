# CSV upload and worker lifecycle

## Data flow

1. The API verifies the shared API key, checks the .csv extension, streams the file to a server-generated name inside UPLOAD_DIR, and enforces the byte limit.
2. The API reads only the header row and detects or validates the logical column mapping. It stores a QUEUED document in validation_runs and returns HTTP 202 with a run ID.
3. The separate worker atomically claims the oldest queued run with find_one_and_update and increments attempt_count.
4. Before persisting any customer records, the worker streams through the full CSV once to check syntax, row-width, cell-length and row-count limits.
5. The existing batch processor reads the file again, processes bounded batches, persists row-level findings in customer_records, updates DP rollups in dp_records, and updates run counters.
6. On COMPLETED or FAILED, the worker removes the stored upload and removes its internal file key from the run document. The source file on the user's computer is not changed.

## Run states

- QUEUED: accepted by the API, waiting for a worker.
- PROCESSING with stage PREFLIGHT: CSV syntax and size rules are being checked.
- PROCESSING with stage VALIDATING: row batches are being processed; progress is saved in MongoDB.
- COMPLETED: all accepted source rows were handled, with blank rows counted separately.
- FAILED: the CSV or processing operation failed. If a batch was partially written, those records remain associated with this failed run.

## Operational boundaries

- GEOCHECK_API_KEY must be configured; a missing key disables upload and run-status endpoints.
- Default upload limit: 512 MiB. Default maximum source rows: 1,000,000.
- Run the API and worker against the same MongoDB database and same persistent UPLOAD_DIR.
- Run only one worker process for this MVP. MongoDB claims are atomic, but multi-worker lease/heartbeat coordination is not production-hardened.
- Queued jobs survive process restarts as long as their MongoDB documents and uploaded files persist.
- A processing run that reports no progress for the configured stale timeout is marked FAILED with recovery_action=REUPLOAD_REQUIRED. The associated partial results are not deleted automatically; inspect results only with the failed run status in mind.
- Automatic resume/replay is intentionally not implemented: restarting from the beginning could violate the unique source-row index or double-count DP rollups. Submit the source CSV again as a new run.
- The stored CSV is deleted after a terminal result or stale-run handling. There is no automatic cleanup for orphaned .part files after an abrupt API crash yet.

## Next engineering tasks

1. Add restart-safe/idempotent processing, with per-run staging or deterministic upserts and accurate rollback semantics.
2. Add live MongoDB integration tests for unique indexes, atomic job claims, failure injection and cleanup.
3. Add authenticated user identity/authorization, limits/rate controls, encryption/retention policy and audited access before real customer data.
4. Benchmark 600,000 rows and 150,000 DPs on a representative environment with memory, throughput, index size and reconciliation reports.
