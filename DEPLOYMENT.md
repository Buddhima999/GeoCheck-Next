# Deployment: Vercel Hobby + MongoDB Atlas

## Deployment layout

- **Vercel Hobby:** serves the React/Vite frontend from `frontend/`.
- **MongoDB Atlas:** stores run metadata, customers, FDPs, and cached town zones.
- **Persistent API/worker host:** runs the FastAPI API and the separate CSV worker, with one shared persistent upload directory. Keep the API and worker on the same host/volume (the included Docker Compose setup does this).

The API and worker cannot be replaced by a Vercel frontend deployment. The worker is a continuously running MongoDB queue consumer, and API uploads are read later by that worker from disk. Vercel Functions have read-only filesystems except for temporary `/tmp` storage, and their work is bounded by function execution limits. Do not point the production UI at a Vercel-only API unless CSV storage and background processing have first been redesigned around durable object storage and a managed queue.

## 1. Prepare MongoDB Atlas

1. Create an Atlas database and a database user with access only to the GeoCheck database.
2. Configure Atlas Network Access to allow connections from the API/worker host. Avoid `0.0.0.0/0` for real customer data where possible.
3. Copy the Atlas connection string. URL-encode special characters in the username/password.
4. Set `MONGO_URI` to that connection string and `MONGO_DB_NAME=geocheck_next` on both API and worker.

The app creates its required indexes when the API starts. Atlas storage and compute sizing must be based on a benchmark using representative customer/DP volume; no production capacity claim is made by this repository.

## 2. Run API and worker on a persistent host

Use a host that can keep both processes running and gives them the same persistent filesystem. The dedicated `docker-compose.atlas.yml` file runs the API and worker against Atlas and shares the persistent upload volume without starting a local MongoDB container. Do not run the worker as a one-shot request or as a Vercel Function.

Set these environment values on both processes where applicable:

| Variable | Purpose |
| --- | --- |
| `MONGO_URI` | MongoDB Atlas connection string |
| `MONGO_DB_NAME` | Database name, normally `geocheck_next` |
| `GEOCHECK_API_KEY` | Long, randomly generated API access secret; same value the authorized UI users enter |
| `UPLOAD_DIR` | Shared persistent path visible to both API and worker |
| `REFERENCE_DATA_DIR` | Path to the checked-in reference data directory |
| `API_CORS_ORIGINS` | Exact Vercel production origin, e.g. `https://your-project.vercel.app` |

Copy the root environment template and fill the deployment values, then start the API and worker with:

~~~powershell
Copy-Item .env.example .env
# Edit .env: set MONGO_URI to the Atlas URI and replace GEOCHECK_API_KEY.
docker compose -f docker-compose.atlas.yml up --build -d
~~~

Also configure a persistent volume for uploads, HTTPS, backups, log rotation, and a health check at `/api/health`. The API and worker use the same named upload volume. The app currently supports one worker process for this MVP.

## 3. Deploy the frontend to Vercel Hobby

1. Import this GitHub repository into Vercel.
2. Set **Root Directory** to `frontend`.
3. Use the default Vite build command `npm run build` and output directory `dist`.
4. Add these frontend environment variables for Production (and Preview only if the preview API is separately protected):

   - `VITE_API_BASE_URL=https://your-api.example.com` (no trailing slash)
   - `VITE_GOOGLE_MAPS_API_KEY=...` if using Google Maps
   - `VITE_MAP_TILE_URL=...` only if using a custom OpenStreetMap-compatible tile service

5. Deploy. `frontend/vercel.json` routes client-side page refreshes back to the SPA.
6. Set the API host's `API_CORS_ORIGINS` to the exact deployed Vercel origin, then restart the API.

`VITE_*` values are embedded in public browser code. The Google Maps key is necessarily visible to users; restrict it in Google Cloud by website referrer and the Maps JavaScript API. Do not put Atlas credentials, the API host's private credentials, or a server-side Google key in frontend variables.

## Security note before real customer data

The current frontend asks the user for a shared `GEOCHECK_API_KEY`, and the browser must send it to the API. This is a basic access gate, not per-user authentication; every authorized browser can inspect that key. Before exposing real subscriber data to multiple internet users, add an identity provider and role-based authorization, then remove the shared browser secret. Keep Atlas and the API private where possible and apply approved retention/deletion policies.

## Local build and deployment checks

From `frontend/`, run `npm ci` and `npm run build`. The API and worker require the same `MONGO_URI`, database, reference data and persistent uploads directory. The full deployment is not considered production-ready until the exact target host, Atlas tier, authentication, backups, and a representative high-volume benchmark have been verified.
