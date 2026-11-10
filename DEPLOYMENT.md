# Deployment: Vercel Hobby + MongoDB Atlas

## Deployment layout

- **Frontend Vercel project:** Root Directory `frontend/`, using Vite.
- **Backend Vercel project:** Root Directory `backend/`, using Vercel's Python/FastAPI runtime.
- **MongoDB Atlas:** stores run metadata, customers, FDPs, and cached town zones.

The FastAPI API can run on Vercel without Docker. However, the current CSV analysis pipeline is **not fully compatible with Vercel Hobby**: Vercel Functions have a 4.5 MB request-body limit, but this app allows uploads up to 512 MiB; the CSV worker is a continuously running process that Vercel does not keep running; and files written by a function are temporary. Deploying the two projects alone will not make the existing large-CSV upload and background-analysis flow work. Full Vercel support needs direct-to-Blob uploads and a durable Vercel Queue/Workflow consumer with chunked, retry-safe processing. Do not treat CSV uploads as operational on this deployment until that migration is implemented.

## 1. Prepare MongoDB Atlas

1. Create an Atlas database and a database user with access only to the GeoCheck database.
2. Vercel Hobby functions use dynamic outbound IP addresses; Vercel Static IPs are a Pro/Enterprise feature. Atlas IP allowlisting therefore cannot be narrowly pinned to a Hobby function. Use a plan/network option with static egress for production rather than broadly opening Atlas access.
3. Copy the Atlas connection string. URL-encode special characters in the username/password.
4. Set `MONGO_URI` to that connection string and `MONGO_DB_NAME=geocheck_next` in the backend Vercel project.

The app creates its required indexes when the API starts. Atlas storage and compute sizing must be based on a benchmark using representative customer/DP volume; no production capacity claim is made by this repository.

## 2. Deploy the backend API to Vercel

Create another Vercel project from this repository and set **Root Directory** to `backend`. Vercel detects the FastAPI app at `app/main.py`, uses `requirements.txt`, and applies `vercel.json`.

Set these **Production** environment variables in the backend project's Settings:

| Variable | Purpose |
| --- | --- |
| `MONGO_URI` | MongoDB Atlas connection string |
| `MONGO_DB_NAME` | Database name, normally `geocheck_next` |
| `GEOCHECK_API_KEY` | Long, randomly generated API access secret; same value the authorized UI users enter |
| `API_CORS_ORIGINS` | Exact frontend Vercel origin, e.g. `https://your-frontend.vercel.app` |
| `REFERENCE_DATA_DIR` | `reference-data` |

Deploy and copy the backend URL, for example `https://your-backend.vercel.app`. Visit `https://your-backend.vercel.app/api/health`; it should report that MongoDB is connected. The upload endpoint explicitly returns an error in a deployed Vercel environment rather than creating a queued run that has no worker to process it. CSV processing is not ready until the storage and queue migration described above is done.

To run the existing CSV workflow before that migration, the API and worker must run on a persistent service with shared upload storage. They may run without Docker on a suitable host; the included `docker-compose.atlas.yml` is only an optional container setup.

## 3. Deploy the frontend to Vercel

1. Import the same GitHub repository as a second Vercel project.
2. Set **Root Directory** to `frontend`.
3. Use the default Vite build command `npm run build` and output directory `dist`.
4. Add these frontend environment variables for Production (and Preview only if the preview API is separately protected):

   - `VITE_API_BASE_URL=https://your-backend.vercel.app` (no trailing slash)
   - `VITE_GOOGLE_MAPS_API_KEY=...` if using Google Maps
   - `VITE_MAP_TILE_URL=...` only if using a custom OpenStreetMap-compatible tile service

5. Deploy. `frontend/vercel.json` routes client-side page refreshes back to the SPA.
6. Set the backend project's `API_CORS_ORIGINS` to the exact frontend URL and redeploy the backend.

`VITE_*` values are embedded in public browser code. The Google Maps key is necessarily visible to users; restrict it in Google Cloud by website referrer and the Maps JavaScript API. Do not put Atlas credentials, the API host's private credentials, or a server-side Google key in frontend variables.

## Security note before real customer data

The current frontend asks the user for a shared `GEOCHECK_API_KEY`, and the browser must send it to the API. This is a basic access gate, not per-user authentication; every authorized browser can inspect that key. Before exposing real subscriber data to multiple internet users, add an identity provider and role-based authorization, then remove the shared browser secret. Keep Atlas and the API private where possible and apply approved retention/deletion policies.

Vercel Hobby functions run for at most 300 seconds and reject request bodies above 4.5 MB. Hobby cron jobs run at most once per day, so they cannot replace the current polling worker. Hobby is intended for personal, non-commercial use. See [function duration](https://vercel.com/docs/functions/configuring-functions/duration), [payload limit](https://vercel.com/docs/errors/function_payload_too_large), [cron limits](https://vercel.com/docs/cron-jobs/usage-and-pricing), and [Hobby plan](https://vercel.com/docs/plans/hobby).

Vercel uses dynamic outbound IP addresses by default; stable Static IP egress is available on Pro and Enterprise. See [Vercel outbound IP guidance](https://vercel.com/kb/guide/can-i-get-a-fixed-ip-address).

## Local build and deployment checks

From `frontend/`, run `npm ci` and `npm run build`. The API and worker require the same `MONGO_URI`, database, reference data and persistent uploads directory. The full deployment is not considered production-ready until the exact target host, Atlas tier, authentication, backups, and a representative high-volume benchmark have been verified.
