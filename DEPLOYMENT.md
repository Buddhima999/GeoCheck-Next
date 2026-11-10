# Deployment: Vercel + MongoDB Atlas

GeoCheck uses two Vercel projects and one private Blob store:

1. Frontend (`frontend/`): Vite app and the secure Blob upload-token function.
2. Backend (`backend/`): FastAPI API and MongoDB access.
3. CSV worker: a Vercel Daemon configured in the backend project's `backend/vercel.json`, built from `backend/Dockerfile` and deployed alongside the backend API.
4. MongoDB Atlas stores queued runs and processed results; private Vercel Blob temporarily stores source CSVs.

The worker and Blob flow are intended for short initial demonstrations with synthetic data. **Vercel Hobby is limited to personal, non-commercial use and is not suitable for company customer records.** The Hobby account currently shows 4 Fluid CPU-hours and 360 GB-hours of provisioned memory per month. Monitor the Vercel Usage page because the daemon shares the account's Fluid quotas. See [Vercel Daemons](https://vercel.com/docs/daemons) and the [Hobby plan](https://vercel.com/docs/plans/hobby).

## 1. Prepare MongoDB Atlas

1. Create a database user with access only to the GeoCheck database.
2. Set Atlas Network Access to allow connections from the Vercel projects. Vercel Hobby uses dynamic outbound IP addresses, so it cannot be tightly allowlisted by a stable IP. Do not expose real company data through a broadly open database rule.
3. Use the Atlas connection string for `MONGO_URI` and set `MONGO_DB_NAME=geocheck_next`.

The app creates its indexes when the API starts. Benchmark Atlas with representative data before relying on it for large datasets.

## 2. Deploy the backend API

Create/import a Vercel project from this repository with **Root Directory** set to `backend`. Keep the existing FastAPI configuration.

Set these Production environment variables:

| Variable | Purpose |
| --- | --- |
| `MONGO_URI` | MongoDB Atlas connection string |
| `MONGO_DB_NAME` | Database name, normally `geocheck_next` |
| `GEOCHECK_API_KEY` | Long random key entered by authorized app users |
| `API_CORS_ORIGINS` | Exact frontend Vercel origin |
| `BLOB_READ_WRITE_TOKEN` | Same private Blob store token used by the frontend and worker; allows cleanup of abandoned uploads |
| `REFERENCE_DATA_DIR` | `reference-data` |

Deploy and confirm `https://<backend-domain>/api/health` reports MongoDB connected. The shared API key is a basic gate only, not user-specific authentication.

## 3. Configure the frontend and private Blob store

Create/import a second project from the same repository with **Root Directory** set to `frontend`, Vite build command `npm run build`, and output directory `dist`.

Create a **private** Vercel Blob store and connect its `BLOB_READ_WRITE_TOKEN` to the frontend project. Copy that same token into the backend project for cleanup and later into the worker project. The frontend's server environment must also contain:

| Variable | Purpose |
| --- | --- |
| `VITE_API_BASE_URL` | Backend URL, e.g. `https://<backend-domain>` |
| `GEOCHECK_API_KEY` | Same secret as the backend, for issuing authenticated short-lived Blob upload tokens |
| `VITE_GOOGLE_MAPS_API_KEY` | Optional browser map key; restrict it by referrer and API in Google Cloud |

`VITE_*` settings are public browser values. Keep API keys, Blob tokens, and database credentials out of `VITE_*` variables. The frontend parses only the CSV header locally, uploads file contents directly to private Blob, and then asks the API to enqueue that Blob object.

Set `API_CORS_ORIGINS` in the backend to the exact frontend origin and redeploy the backend.

## 4. Run the CSV worker as a Vercel Daemon

The daemon is part of the existing backend Vercel project, not a third project. `backend/vercel.json` declares one `geocheck-worker` instance using `backend/Dockerfile` and runs `python -m app.worker`. Keep the backend project's Root Directory set to `backend/` and deploy the updated `main` branch to Production. Daemons are production-only during beta. Check that project's Daemon logs and confirm the worker reports that it started.

The backend project's existing `MONGO_URI` and `MONGO_DB_NAME` variables are shared with the daemon. Add the same `BLOB_READ_WRITE_TOKEN` from the private Blob store and set `UPLOAD_DIR=/data/uploads` in the backend project so the worker can download source files into its writable container directory. The frontend and backend must both be connected to the same private Blob store. Copy optional validation settings such as `GOOGLE_MAPS_SERVER_KEY` only if the worker needs them.

The worker polls MongoDB for queued runs, downloads each source CSV from private Blob to temporary local storage, processes it in batches, writes results to MongoDB, then deletes the source Blob. Its local disk is temporary; MongoDB and Blob are the durable stores.

The daemon instance is configured in `sin1` (Singapore), close to Sri Lanka and common Atlas Asia regions. Hobby daemon instances consume the account's monthly CPU and provisioned-memory quotas; monitor usage because processing stops when a quota is exhausted. Change the configured region only to a Daemon-supported region.

## Upload and worker behavior

- Large CSV contents never pass through a Vercel Function, avoiding the 4.5 MB function request-body limit.
- The header is read locally in the browser and checked by the API before upload.
- The API stores the Blob pathname and private URL in the MongoDB run record; the private URL is not returned to the browser as a result or log message.
- Completed and failed runs delete their temporary source CSV from Blob. If job creation fails after Blob upload, the frontend requests cleanup.
- Local development keeps the existing multipart upload and local worker flow. The deployed frontend uses direct private Blob upload.

## Important limits

- Vercel Daemons are in public beta, run only in Production, use ephemeral local disk, and cannot receive incoming HTTP requests. They should be treated as restartable consumers; this worker retries stale jobs from the original Blob.
- Hobby is personal/non-commercial, so use only synthetic demo data on it. The Hobby daemon quota is not sized for regular 500,000-customer processing.
- Atlas traffic from Hobby has dynamic egress addresses. Use a secure database network design before handling real subscriber data.
- The shared browser API key can be inspected by authorized users. Add proper identity and role-based access before a real multi-user deployment.

See [Vercel Daemons](https://vercel.com/docs/daemons), [Vercel Blob private storage](https://vercel.com/docs/vercel-blob/private-storage), and [Vercel Function payload limits](https://vercel.com/docs/errors/function_payload_too_large).
