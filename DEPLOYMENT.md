# Production deployment

## Deployment layout

- **Frontend:** Vercel Hobby static site, rooted at `frontend/`.
- **API and CSV worker:** one always-on Render Python web service, rooted at `backend/`.
- **Queue and analysis results:** MongoDB Atlas.
- **Temporary source CSV files:** a persistent disk attached to the Render service.

The API and worker run in one Python service, so they see the same uploaded file. The worker claims queued runs from MongoDB, processes them in batches, writes results to Atlas, and removes the source CSV when finished. The service must remain at one instance because the attached disk is private to that instance.

## Deploy the API and worker on Render

1. In Render, create a **Blueprint** from this GitHub repository and select the root `render.yaml` file. The blueprint uses the native Python runtime; Docker is not required.
2. Render will request the secret values for `MONGO_URI` and `GEOCHECK_API_KEY`. Enter the Atlas connection string and the same long random key already used by the GeoCheck workspace. Do not put either value in a `VITE_*` frontend variable.
3. Review the service and its cost before creating it. This configuration uses a paid always-on web service and a persistent disk; Render does not provide persistent disks on its Free web services.
4. In MongoDB Atlas **Network Access**, allow the outbound IP ranges shown for this Render service's Singapore region. Avoid opening the cluster to all IP addresses. Render documents the service IP ranges in its Connect menu: [Render outbound IP addresses](https://render.com/docs/outbound-ip-addresses).
5. Wait until the service is live. Verify `https://<render-service>.onrender.com/api/health`. It should return `status: ok`, `database: connected`, and `worker: running`.

The blueprint mounts a 1 GB disk at `/var/data`, stores uploads in `/var/data/uploads`, caps each CSV at 512 MiB and one million rows, and runs a single worker thread in the API process. Increase the disk size before accepting a larger backlog. A Render disk can be attached to only one service instance, and the service cannot be scaled horizontally while it is attached: [Render persistent disk limits](https://render.com/docs/disks).

## Point the Vercel frontend at Render

After Render gives the API its `onrender.com` URL:

1. Open the `geo-check-next-frontend` Vercel project → **Settings** → **Environment Variables**.
2. Set `VITE_API_BASE_URL` to `https://<render-service>.onrender.com` for Production (no trailing slash). This is a public API address, not a secret.
3. In Render, set `API_CORS_ORIGINS` to `https://geo-check-next-frontend.vercel.app` (add any approved custom frontend domain as a comma-separated origin).
4. Redeploy both services so their new environment values take effect. The Git-linked Render service and Vercel frontend deploy automatically when their tracked source changes.
5. Reload the frontend. Its sign-in panel should show **Local services are ready**. Enter the configured `GEOCHECK_API_KEY` in the workspace key field to use protected analysis functions.

The existing Vercel backend can remain deployed, but the frontend must point to the Render API for CSV uploads. The key field is a browser-session access gate; it is not stored in Vercel frontend code. Do not set the secret as `VITE_GEOCHECK_API_KEY`, because Vite embeds `VITE_*` variables into public browser JavaScript.

## Local development

Set `RUN_CSV_WORKER=true` in `backend/.env` only if you want the API process to run its worker thread. The existing `docker-compose.yml` starts a separate worker service, so leave this flag `false` when using that compose setup to avoid running two workers in one environment. For development without Docker, start the API and worker separately as documented in [README.md](README.md).

## Limits and production readiness

- Vercel still hosts the frontend; it no longer receives the large CSV upload. This avoids Vercel Functions' 4.5 MB request-body limit.
- The Render persistent disk is attached to one service instance. Do not set multiple Uvicorn workers or scale the service horizontally.
- Atlas capacity and Render disk capacity should be measured with representative CSVs. The app stores analysis rows in Atlas; the source CSV is removed after processing or run deletion.
- The current shared workspace API key is a basic gate, not individual user authentication. Add identity and role-based access before exposing real customer records to multiple users.
- Vercel Hobby is intended for personal, non-commercial use. Confirm the plan is appropriate before using this for company operations: [Vercel Hobby plan](https://vercel.com/docs/plans/hobby).
