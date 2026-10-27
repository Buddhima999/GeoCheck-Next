from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import PyMongoError

from .api.validation_runs import router as validation_runs_router
from .config import settings
from .database import client, ensure_indexes, ping_database


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

    yield
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


@app.get("/api/health")
def health() -> dict[str, str]:
    try:
        ping_database()
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.") from exc
    return {"status": "ok", "database": "connected", "service": "geocheck-next"}
