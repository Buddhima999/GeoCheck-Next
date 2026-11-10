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
from typing import Any, Callable

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from .config import settings
from .database import client, customer_records, dp_records, ensure_indexes, ping_database, town_zone_cache, validation_runs
from .upload_storage import remove_upload, resolve_upload_path
from .reference_data import ReferenceData, load_reference_data
from .validation.batch_processor import process_csv_file
from .validation.csv_import import iter_mapped_rows
from .validation.google_maps import GoogleMapsClient
from .validation.osm_town_zones import NominatimTownZoneClient
from .validation.rules import ValidationSettings

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
    progress_callback: Callable[[int], None] | None = None,
) -> int:
    """Stream-check CSV syntax and limits before writing customer rows."""
    source_rows = 0
    with csv_path.open("r", newline="", encoding="utf-8-sig") as stream:
        for _ in iter_mapped_rows(
            stream,
            mapping,
            max_rows=max_rows,
            include_blank_rows=True,
        ):
            source_rows += 1
            if progress_callback and source_rows % 10_000 == 0:
                progress_callback(source_rows)
    if progress_callback:
        progress_callback(source_rows)
    return source_rows


def mark_stale_processing_runs(
    run_collection: Collection,
    *,
    stale_after_seconds: int,
    max_attempts: int = 2,
) -> int:
    """Requeue stale work once for a clean restart; fail and clean up after max attempts."""
    if stale_after_seconds < 1:
        raise ValueError("stale_after_seconds must be at least 1")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

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
    changed_count = 0
    for run in run_collection.find(
        stale_query,
        {
            "run_id": 1,
            "input_file_key": 1,
            "last_progress_at": 1,
            "processing_started_at": 1,
            "attempt_count": 1,
        },
    ):
        run_id = run.get("run_id")
        if not run_id:
            continue
        # Guard against updating a run that made progress after the stale query.
        guard: dict[str, Any] = {"run_id": run_id, "status": "PROCESSING"}
        if run.get("last_progress_at") is not None:
            guard["last_progress_at"] = run["last_progress_at"]
        else:
            guard["last_progress_at"] = {"$exists": False}
            if run.get("processing_started_at") is not None:
                guard["processing_started_at"] = run["processing_started_at"]

        now = utc_now()
        attempts = int(run.get("attempt_count", 1) or 1)
        if attempts < max_attempts and run.get("input_file_key"):
            update = {
                "$set": {
                    "status": "QUEUED",
                    "stage": "REQUEUED_AFTER_STALE",
                    "restart_from_beginning": True,
                    "restart_requested_at": now,
                    "last_error": (
                        "The worker stopped reporting progress. This run will be retried from the "
                        "beginning after clearing its partial results."
                    ),
                },
                "$unset": {
                    "processing_started_at": "",
                    "last_progress_at": "",
                    "worker_id": "",
                    "worker_claimed_at": "",
                },
            }
        else:
            update = {
                "$set": {
                    "status": "FAILED",
                    "stage": "INTERRUPTED",
                    "failed_at": now,
                    "recovery_action": "REUPLOAD_REQUIRED",
                    "last_error": (
                        "The worker stopped reporting progress and the retry limit was reached. "
                        "Partial records remain attached to this failed run; upload the source CSV "
                        "again to create a new run."
                    ),
                },
                "$unset": {"input_file_key": ""},
            }
        result = run_collection.update_one(guard, update)
        if getattr(result, "matched_count", 0):
            changed_count += 1
            if update["$set"]["status"] == "FAILED":
                remove_upload(run.get("input_file_key"))
    return changed_count

def process_next_queued_run(
    *,
    run_collection: Collection = validation_runs,
    customer_collection: Collection = customer_records,
    dp_collection: Collection = dp_records,
    worker_id: str | None = None,
    batch_size: int | None = None,
    max_rows: int | None = None,
    reference_data: ReferenceData | None = None,
) -> bool:
    """Claim one job and restart it from a clean per-run result set before processing."""
    identity = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    run = claim_next_run(run_collection, identity)
    if run is None:
        return False

    run_id = str(run.get("run_id", ""))
    file_key = run.get("input_file_key")
    terminal_status = "FAILED"
    google_maps_client: GoogleMapsClient | None = None
    osm_town_client: NominatimTownZoneClient | None = None
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

        def record_preflight_progress(rows_scanned: int) -> None:
            run_collection.update_one(
                {"run_id": run_id, "status": "PROCESSING"},
                {"$set": {"last_progress_at": utc_now(), "preflight_rows_scanned": rows_scanned}},
            )

        preflight_count = preflight_csv_file(
            csv_path,
            mapping,
            max_rows=configured_max_rows,
            progress_callback=record_preflight_progress,
        )

        # Clear previous partial output only after the complete source passes preflight.
        customer_collection.delete_many({"run_id": run_id})
        dp_collection.delete_many({"run_id": run_id})
        restart_fields = {
            "stage": "VALIDATING",
            "preflight_source_rows": preflight_count,
            "rows_processed": 0,
            "source_rows_seen": 0,
            "blank_rows_skipped": 0,
            "batches_processed": 0,
            "rows_with_findings": 0,
            "rows_with_hard_errors": 0,
            "issue_counts": {},
            "last_progress_at": utc_now(),
            "last_error": None,
        }
        unset_fields = {
            "summary": "",
            "completed_at": "",
            "failed_at": "",
            "partial_rows_processed": "",
            "partial_source_rows_seen": "",
            "partial_blank_rows_skipped": "",
            "partial_batches_processed": "",
            "recovery_action": "",
            "restart_from_beginning": "",
        }
        if reference_data is not None:
            ref_status = reference_data.status()
            restart_fields["reference_versions"] = reference_data.versions
            restart_fields["reference_status"] = {
                key: value
                for key, value in ref_status.items()
                if key != "warning" and key != "directory"
            }
        run_collection.update_one(
            {"run_id": run_id, "status": "PROCESSING"},
            {"$set": restart_fields, "$unset": unset_fields},
        )
        callbacks = reference_data.callbacks() if reference_data is not None else {}
        if settings.osm_town_lookup_enabled and settings.osm_nominatim_email:
            osm_town_client = NominatimTownZoneClient(
                town_zone_cache,
                base_url=settings.osm_nominatim_url,
                email=settings.osm_nominatim_email,
                max_lookups=settings.osm_town_max_lookups_per_run,
            )
            run_collection.update_one(
                {"run_id": run_id, "status": "PROCESSING"},
                {"$set": {"stage": "ADDRESS_ZONE_PREPARATION", "last_progress_at": utc_now()}},
            )
            selected_towns = osm_town_client.prepare(
                str(csv_path), mapping, max_rows=configured_max_rows,
            )
            local_address_zone_check = callbacks.get("address_zone_check")

            def combined_address_zone_check(address: str, lat: float, lon: float) -> bool | None:
                local = local_address_zone_check(address, lat, lon) if local_address_zone_check else None
                return local if local is not None else osm_town_client.check(address, lat, lon)

            callbacks["address_zone_check"] = combined_address_zone_check
            logger.info("Address-zone lookup enabled for run %s: selected %s common towns.", run_id, selected_towns)
        elif settings.osm_town_lookup_enabled:
            logger.warning("OSM town lookup is enabled but OSM_NOMINATIM_EMAIL is missing; address checks will use local references only.")
        distance_method = str(settings.distance_method).strip().casefold()
        if distance_method not in {"straight_line", "google_routes"}:
            raise ValueError("DISTANCE_METHOD must be straight_line or google_routes.")
        if settings.google_maps_server_key and (distance_method == "google_routes" or settings.google_geocoding_enabled):
            google_maps_client = GoogleMapsClient(
                settings.google_maps_server_key,
                max_requests=settings.google_maps_max_requests_per_run,
                timeout_seconds=settings.google_maps_timeout_seconds,
            )
        if distance_method == "google_routes" and google_maps_client is not None:
            callbacks["route_distance_check"] = google_maps_client.route_distance_m
        if settings.google_geocoding_enabled and google_maps_client is not None:
            callbacks["geocode_address_check"] = google_maps_client.address_distance_m

        validation_settings = ValidationSettings(
            customer_dp_limit_m=settings.customer_dp_limit_m,
            max_line_distance_m=settings.max_line_distance_m,
            distance_method=distance_method,
            address_match_tolerance_m=settings.google_address_match_tolerance_m,
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
            settings=validation_settings,
            dp_port_capacity=settings.dp_port_capacity,
            dp_association_min_pass_fraction=settings.dp_association_min_pass_fraction,
            **callbacks,
        )
        terminal_status = str(summary.get("status", "FAILED"))
        if osm_town_client is not None:
            summary["osm_town_lookup_enabled"] = True
            summary["osm_town_lookup_queries"] = osm_town_client.queries_used
            run_collection.update_one({"run_id": run_id}, {"$set": {"summary": summary}})
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
        if google_maps_client is not None:
            try:
                google_maps_client.close()
            except Exception:
                logger.warning("Could not close Google Maps client for run %s", run_id or "unknown")
        if osm_town_client is not None:
            try:
                osm_town_client.close()
            except Exception:
                logger.warning("Could not close Nominatim client for run %s", run_id or "unknown")
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
    reference_data = load_reference_data()
    logger.info("GeoCheck validation worker started.")
    try:
        while True:
            try:
                mark_stale_processing_runs(
                    validation_runs,
                    stale_after_seconds=settings.worker_stale_after_seconds,
                    max_attempts=settings.worker_max_attempts,
                )
                did_work = process_next_queued_run(reference_data=reference_data)
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
