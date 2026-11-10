from contextlib import asynccontextmanager
import logging
import threading
from threading import Thread

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import PyMongoError

from .api.reference_data import router as reference_data_router
from .api.results import router as validation_results_router
from .api.validation_runs import router as validation_runs_router
from .config import settings
from .database import client, ensure_indexes, ping_database

logger = logging.getLogger("geocheck.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ping_database()
        ensure_indexes()
    except PyMongoError as exc:
        raise RuntimeError(
            "GeoCheck Next could not connect to MongoDB. Verify MONGO_URI "
            "and database network access."
        ) from exc

    worker_stop: threading.Event | None = None
    worker_thread: Thread | None = None
    if settings.run_csv_worker:
        # On the single-service Render deployment, the API and queue worker
        # share the same persistent disk and MongoDB connection pool.
        from .worker import run_worker

        worker_stop = threading.Event()
        worker_thread = Thread(
            target=run_worker,
            kwargs={"stop_event": worker_stop, "close_client": False},
            name="geocheck-csv-worker",
            daemon=True,
        )
        worker_thread.start()
        app.state.worker_thread = worker_thread
        app.state.worker_stop = worker_stop
        logger.info("CSV worker enabled in the API service.")

    yield
    if worker_stop is not None:
        worker_stop.set()
    if worker_thread is not None:
        # Let the active run finish cleanly before a service restart. Render's
        # shutdown grace should be configured to the same maximum (300s).
        worker_thread.join(timeout=300)
    client.close()


app = FastAPI(
    title="GeoCheck Next API",
    version="0.1.0",
    description="Scalable customer and DP location quality API.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)

app.include_router(validation_runs_router)
app.include_router(validation_results_router)
app.include_router(reference_data_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    try:
        ping_database()
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.") from exc
    worker_thread = getattr(app.state, "worker_thread", None)
    worker_status = "running" if worker_thread is not None and worker_thread.is_alive() else "disabled"
    return {
        "status": "ok",
        "database": "connected",
        "worker": worker_status,
        "service": "geocheck-next",
    }
