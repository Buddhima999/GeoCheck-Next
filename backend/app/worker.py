"""MongoDB-backed queue worker for CSV validation runs.

Start with: python -m app.worker
Run one worker process for this MVP. Queued jobs survive API/worker restarts. A job
that was interrupted after processing began is marked FAILED and must be re-uploaded;
partial writes are not automatically resumed.
"""
from __future__ import annotations

import logging
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from .config import settings
from .database import client, customer_records, dp_records, ensure_indexes, ping_database, validation_runs
from .upload_storage import remove_upload, resolve_upload_path
from .validation.batch_processor import process_csv_file
from .validation.csv_import import iter_mapped_rows

logger = logging.getLogger("geocheck.worker")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def claim_next_run(run_collection: Collection, worker_id: str) -> dict[str, Any] | None:
    """Atomically claim the oldest queued run so two workers do not take the same job."""
    now = utc_now()
    return run_collection.find_one_and_update(
        {"status": "QUEUED"},
        {
            "$set": {
                "status": "PROCESSING",
                "stage": "CLAIMED",
                "worker_id": worker_id,
                "worker_claimed_at": now,
                "processing_started_at": now,
                "last_progress_at": now,
                "last_error": None,
            },
            "$inc": {"attempt_count": 1},
        },
        sort=[("created_at", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )


def preflight_csv_file(
    csv_path: Path,
    mapping: dict[str, str],
    *,
    max_rows: int,
) -> int:
    """Stream-check CSV syntax, row width and field limits before writing customer rows."""
    source_rows = 0
    with csv_path.open("r", newline="", encoding="utf-8-sig") as stream:
        for _ in iter_mapped_rows(
            stream,
            mapping,
            max_rows=max_rows,
            include_blank_rows=True,
        ):
            source_rows += 1
    return source_rows


def mark_stale_processing_runs(
    run_collection: Collection,
    *,
    stale_after_seconds: int,
) -> int:
    """Fail interrupted jobs after a no-progress timeout; do not replay partial writes."""
    if stale_after_seconds < 1:
        raise ValueError("stale_after_seconds must be at least 1")

    cutoff = utc_now() - timedelta(seconds=stale_after_seconds)
    stale_query = {
        "status": "PROCESSING",
        "$or": [
            {"last_progress_at": {"$lt": cutoff}},
            {
                "last_progress_at": {"$exists": False},
                "processing_started_at": {"$lt": cutoff},
            },
        ],
    }
    failed_count = 0
    for run in run_collection.find(
        stale_query,
        {"run_id": 1, "input_file_key": 1, "last_progress_at": 1, "processing_started_at": 1},
    ):
        run_id = run.get("run_id")
        if not run_id:
            continue
        # Guard against failing a run that made progress after the stale query.
        guard: dict[str, Any] = {"run_id": run_id, "status": "PROCESSING"}
        if run.get("last_progress_at") is not None:
            guard["last_progress_at"] = run["last_progress_at"]
        else:
            guard["last_progress_at"] = {"$exists": False}
            if run.get("processing_started_at") is not None:
                guard["processing_started_at"] = run["processing_started_at"]

        now = utc_now()
        result = run_collection.update_one(
            guard,
            {
                "$set": {
                    "status": "FAILED",
                    "stage": "INTERRUPTED",
                    "failed_at": now,
                    "recovery_action": "REUPLOAD_REQUIRED",
                    "last_error": (
                        "The worker stopped reporting progress. Partial writes were not resumed; "
                        "submit the source CSV again as a new run."
                    ),
                },
                "$unset": {"input_file_key": ""},
            },
        )
        if getattr(result, "matched_count", 0):
            remove_upload(run.get("input_file_key"))
            failed_count += 1
    return failed_count


def process_next_queued_run(
    *,
    run_collection: Collection = validation_runs,
    customer_collection: Collection = customer_records,
    dp_collection: Collection = dp_records,
    worker_id: str | None = None,
    batch_size: int | None = None,
    max_rows: int | None = None,
) -> bool:
    """Claim and process one job. Return False when the queue contains no job."""
    identity = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    run = claim_next_run(run_collection, identity)
    if run is None:
        return False

    run_id = str(run.get("run_id", ""))
    file_key = run.get("input_file_key")
    terminal_status = "FAILED"
    try:
        mapping = run.get("column_mapping")
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError("The validation run has no usable column mapping.")
        csv_path = resolve_upload_path(str(file_key or ""))
        if not csv_path.is_file():
            raise FileNotFoundError("The stored CSV file is missing.")
        configured_max_rows = settings.max_csv_rows if max_rows is None else max_rows
        configured_batch_size = settings.worker_batch_size if batch_size is None else batch_size
        run_collection.update_one(
            {"run_id": run_id, "status": "PROCESSING"},
            {"$set": {"stage": "PREFLIGHT", "preflight_started_at": utc_now()}},
        )
        preflight_count = preflight_csv_file(csv_path, mapping, max_rows=configured_max_rows)
        run_collection.update_one(
            {"run_id": run_id, "status": "PROCESSING"},
            {"$set": {"stage": "VALIDATING", "preflight_source_rows": preflight_count}},
        )
        summary = process_csv_file(
            csv_path,
            run_id=run_id,
            mapping=mapping,
            validation_runs=run_collection,
            customer_records=customer_collection,
            dp_records=dp_collection,
            batch_size=configured_batch_size,
            max_rows=configured_max_rows,
        )
        terminal_status = str(summary.get("status", "FAILED"))
        logger.info(
            "Validation run %s ended with status=%s rows=%s",
            run_id,
            terminal_status,
            summary.get("rows_processed", 0),
        )
    except Exception as exc:
        # Log type and run ID only; do not write customer fields to application logs.
        logger.error("Validation run %s failed with %s", run_id or "unknown", type(exc).__name__)
        try:
            run_collection.update_one(
                {"run_id": run_id},
                {
                    "$set": {
                        "status": "FAILED",
                        "stage": "FAILED",
                        "failed_at": utc_now(),
                        "last_error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    }
                },
            )
        except PyMongoError:
            # The next worker loop or stale-run check will surface the interrupted job.
            logger.error("Could not persist failure state for run %s", run_id or "unknown")
    finally:
        try:
            current = run_collection.find_one({"run_id": run_id}, {"status": 1})
            if current and current.get("status") in {"COMPLETED", "FAILED"}:
                remove_upload(file_key)
                run_collection.update_one(
                    {"run_id": run_id},
                    {"$unset": {"input_file_key": ""}},
                )
        except PyMongoError:
            logger.error("Could not finish upload cleanup for run %s", run_id or "unknown")

    return True


def run_worker() -> None:
    """Poll the durable queue until the process is stopped."""
    if settings.worker_poll_seconds <= 0:
        raise ValueError("WORKER_POLL_SECONDS must be greater than zero")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    ping_database()
    ensure_indexes()
    logger.info("GeoCheck validation worker started.")
    try:
        while True:
            try:
                mark_stale_processing_runs(
                    validation_runs,
                    stale_after_seconds=settings.worker_stale_after_seconds,
                )
                did_work = process_next_queued_run()
                if not did_work:
                    time.sleep(settings.worker_poll_seconds)
            except KeyboardInterrupt:
                break
            except Exception:
                logger.exception("Worker loop encountered an operational error.")
                time.sleep(settings.worker_poll_seconds)
    finally:
        client.close()
        logger.info("GeoCheck validation worker stopped.")


if __name__ == "__main__":
    run_worker()
