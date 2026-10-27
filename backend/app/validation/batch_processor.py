"""Streaming, bounded-batch CSV validation and MongoDB persistence.

This is the processing service used by a future upload/worker API. It deliberately
does not load the entire customer file into memory. It stores row-level findings
in customer_records and per-DP rollups in dp_records.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any

from pymongo import UpdateOne
from pymongo.collection import Collection

from .csv_import import iter_mapped_rows
from .rules import FAIL, PASS, ValidationSettings, haversine_m, validate_row

# A confirmed error affecting a customer's own location makes that row unsuitable
# as evidence against DP association quality. Distance-to-DP failure itself is not
# excluded: it may be the relationship problem under investigation.
CUSTOMER_LOCATION_ERROR_CODES = frozenset(
    {
        "COORD_MISSING",
        "COORD_NOT_NUMERIC",
        "COORD_OUT_OF_RANGE",
        "COORD_ZERO_PLACEHOLDER",
        "COORD_POSSIBLE_SWAP",
        "COUNTRY_BOUNDARY",
        "ADDRESS_ZONE_MISMATCH",
        "REPORTED_GEOGRAPHY_MISMATCH",
        "PHONE_AREA_MISMATCH",
    }
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def eligible_for_dp_assessment(result: Mapping[str, Any]) -> bool:
    """Whether this row's customer location can be used for DP association scoring."""
    location = result.get("customer_location")
    if not isinstance(location, Mapping):
        return False
    codes = result.get("issue_codes", [])
    if not isinstance(codes, list):
        return False
    return not any(code in CUSTOMER_LOCATION_ERROR_CODES for code in codes)


def _chunks(items, size: int):
    iterator = iter(items)
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            return
        yield batch


def _collection_rows(collection: Collection, query: dict[str, Any], projection: dict[str, int]) -> list[dict[str, Any]]:
    return list(collection.find(query, projection))


def _valid_point(value: object) -> tuple[float, float] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        lat = float(value["lat"])
        lon = float(value["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _group_dp_observations(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for result in results:
        dp_id = result.get("dp_id_normalized")
        if not dp_id:
            continue
        group = groups.setdefault(
            str(dp_id),
            {
                "dp_id": result.get("dp_id"),
                "row_count": 0,
                "valid_locations": [],
                "eligible_customer_rows": 0,
                "eligible_distance_fail_rows": 0,
            },
        )
        group["row_count"] += 1
        checks = result.get("checks", {})
        dp_check = checks.get("dp_coordinates", {}) if isinstance(checks, Mapping) else {}
        point = _valid_point(result.get("dp_location_observed")) if dp_check.get("status") == PASS else None
        if point is not None:
            group["valid_locations"].append(point)
        if eligible_for_dp_assessment(result):
            group["eligible_customer_rows"] += 1
            distance_check = checks.get("customer_dp_distance", {})
            if isinstance(distance_check, Mapping) and distance_check.get("status") == FAIL:
                group["eligible_distance_fail_rows"] += 1
    return groups


def _make_dp_updates(
    *,
    run_id: str,
    groups: dict[str, dict[str, Any]],
    existing: dict[str, dict[str, Any]],
    settings: ValidationSettings,
    timestamp: datetime,
) -> tuple[list[UpdateOne], dict[str, dict[str, Any]]]:
    operations: list[UpdateOne] = []
    summaries: dict[str, dict[str, Any]] = {}

    for normalized_id, group in groups.items():
        prior = existing.get(normalized_id, {})
        anchor = _valid_point(prior.get("dp_location_observed"))
        if anchor is None and group["valid_locations"]:
            anchor = group["valid_locations"][0]

        outlier_count = 0
        if anchor is not None:
            outlier_count = sum(
                1
                for point in group["valid_locations"]
                if haversine_m(anchor[0], anchor[1], point[0], point[1]) > settings.dp_coordinate_tolerance_m
            )
        already_conflicted = prior.get("coordinate_consistency") == "CONFLICT"
        conflict = already_conflicted or outlier_count > 0
        if anchor is None:
            consistency = "UNVERIFIED"
        else:
            consistency = "CONFLICT" if conflict else "CONSISTENT"

        anchor_doc = {"lat": anchor[0], "lon": anchor[1]} if anchor is not None else None
        dp_id = group["dp_id"] or prior.get("dp_id") or normalized_id
        summaries[normalized_id] = {
            "run_id": run_id,
            "dp_id": dp_id,
            "dp_id_normalized": normalized_id,
            "dp_location_observed": anchor_doc,
            "coordinate_consistency": consistency,
            "observed_row_count": group["row_count"],
            "valid_coordinate_observation_count": len(group["valid_locations"]),
            "coordinate_conflict_observation_count": outlier_count,
            "eligible_customer_row_count": group["eligible_customer_rows"],
            "eligible_distance_fail_row_count": group["eligible_distance_fail_rows"],
        }

        set_on_insert = {
            "run_id": run_id,
            "dp_id": dp_id,
            "dp_id_normalized": normalized_id,
            "dp_location_observed": anchor_doc,
            "coordinate_consistency": "UNVERIFIED" if anchor_doc is None else "CONSISTENT",
            "issue_codes": [],
            "created_at": timestamp,
        }
        increments = {
            "observed_row_count": group["row_count"],
            "valid_coordinate_observation_count": len(group["valid_locations"]),
            "coordinate_conflict_observation_count": outlier_count,
            "eligible_customer_row_count": group["eligible_customer_rows"],
            "eligible_distance_fail_row_count": group["eligible_distance_fail_rows"],
        }
        update: dict[str, Any] = {
            "$setOnInsert": set_on_insert,
            "$inc": increments,
            "$set": {"updated_at": timestamp},
        }
        # Fill an earlier coordinate-less DP record when a later valid observation arrives.
        if prior and _valid_point(prior.get("dp_location_observed")) is None and anchor_doc is not None:
            # Avoid conflicting MongoDB update paths across update operators.
            update["$setOnInsert"].pop("dp_location_observed", None)
            update["$setOnInsert"].pop("coordinate_consistency", None)
            update["$set"]["dp_location_observed"] = anchor_doc
            if not conflict:
                update["$set"]["coordinate_consistency"] = "CONSISTENT"
        if conflict:
            # $setOnInsert and $addToSet/$set cannot target the same field.
            update["$setOnInsert"].pop("coordinate_consistency", None)
            update["$setOnInsert"].pop("issue_codes", None)
            update["$set"]["coordinate_consistency"] = "CONFLICT"
            update["$addToSet"] = {"issue_codes": "DP_COORD_CONFLICT"}
            update["$set"]["first_conflict_seen_at"] = prior.get("first_conflict_seen_at") or timestamp

        operations.append(
            UpdateOne(
                {"run_id": run_id, "dp_id_normalized": normalized_id},
                update,
                upsert=True,
            )
        )
    return operations, summaries


def process_csv_file(
    csv_path: str | Path,
    *,
    run_id: str,
    mapping: Mapping[str, str],
    validation_runs: Collection,
    customer_records: Collection,
    dp_records: Collection,
    batch_size: int = 1000,
    settings: ValidationSettings = ValidationSettings(),
    country_check: Callable[[float, float], bool | None] | None = None,
    phone_area_check: Callable[[str, float, float], bool | None] | None = None,
    address_zone_check: Callable[[str, float, float], bool | None] | None = None,
    geography_check: Callable[[Mapping[str, Any], float, float], bool | None] | None = None,
) -> dict[str, Any]:
    """Validate and persist a CSV in bounded batches.

    The caller must create the validation_runs document first. This service updates
    progress/status, but it does not implement queueing, upload storage, or restart
    recovery yet. The small in-memory dataset helper is not used here.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    start_time = utc_now()
    validation_runs.update_one(
        {"run_id": run_id},
        {
            "$set": {
                "status": "PROCESSING",
                "processing_started_at": start_time,
                "last_error": None,
            }
        },
    )

    counters: Counter[str] = Counter()
    rows_processed = 0
    source_rows_seen = 0
    blank_rows_skipped = 0
    batches_processed = 0

    try:
        with Path(csv_path).open("r", newline="", encoding="utf-8-sig") as stream:
            mapped_rows = iter_mapped_rows(stream, mapping, include_blank_rows=True)
            for batch in _chunks(mapped_rows, batch_size):
                source_rows_seen += len(batch)
                blank_rows_skipped += sum(bool(row.get("is_blank")) for row in batch)
                nonblank_batch = [row for row in batch if not row.get("is_blank")]
                results = [
                    validate_row(
                        row,
                        settings=settings,
                        country_check=country_check,
                        phone_area_check=phone_area_check,
                        address_zone_check=address_zone_check,
                        geography_check=geography_check,
                    )
                    for row in nonblank_batch
                ]
                dp_groups = _group_dp_observations(results)
                if dp_groups:
                    existing_rows = _collection_rows(
                        dp_records,
                        {"run_id": run_id, "dp_id_normalized": {"$in": list(dp_groups)}},
                        {
                            "dp_id": 1,
                            "dp_id_normalized": 1,
                            "dp_location_observed": 1,
                            "coordinate_consistency": 1,
                            "first_conflict_seen_at": 1,
                        },
                    )
                    existing = {str(item["dp_id_normalized"]): item for item in existing_rows}
                    operations, _ = _make_dp_updates(
                        run_id=run_id,
                        groups=dp_groups,
                        existing=existing,
                        settings=settings,
                        timestamp=utc_now(),
                    )
                else:
                    operations = []

                documents: list[dict[str, Any]] = []
                for result in results:
                    codes = result.get("issue_codes", [])
                    counters["rows_with_findings"] += int(bool(codes))
                    counters["rows_with_hard_errors"] += int(bool(result.get("has_hard_error")))
                    for code in codes:
                        counters[f"issue:{code}"] += 1

                    documents.append(
                        {
                            "run_id": run_id,
                            "row_number": result.get("row_number"),
                            "customer_id": result.get("customer_id"),
                            "customer_id_normalized": result.get("customer_id_normalized"),
                            "dp_id": result.get("dp_id"),
                            "dp_id_normalized": result.get("dp_id_normalized"),
                            "source_values": result.get("source_values", {}),
                            "customer_location": result.get("customer_location"),
                            "dp_location_observed": result.get("dp_location_observed"),
                            "checks": result.get("checks", {}),
                            "issue_codes": codes,
                            "findings": result.get("findings", []),
                            "has_hard_error": result.get("has_hard_error", False),
                            "eligible_for_dp_assessment": eligible_for_dp_assessment(result),
                            "created_at": utc_now(),
                        }
                    )

                if documents:
                    customer_records.insert_many(documents, ordered=True)
                if operations:
                    dp_records.bulk_write(operations, ordered=False)

                rows_processed += len(documents)
                batches_processed += 1
                progress_update: dict[str, Any] = {
                    "$inc": {
                        "rows_processed": len(documents),
                        "source_rows_seen": len(batch),
                        "blank_rows_skipped": sum(bool(row.get("is_blank")) for row in batch),
                        "batches_processed": 1,
                        "rows_with_findings": sum(bool(item.get("issue_codes")) for item in documents),
                        "rows_with_hard_errors": sum(bool(item.get("has_hard_error")) for item in documents),
                    },
                    "$set": {"last_progress_at": utc_now()},
                }
                issue_delta = Counter(
                    code for result in results for code in result.get("issue_codes", [])
                )
                for code, value in issue_delta.items():
                    progress_update["$inc"][f"issue_counts.{code}"] = value
                validation_runs.update_one({"run_id": run_id}, progress_update)

        completed = utc_now()
        summary = {
            "rows_processed": rows_processed,
            "source_rows_seen": source_rows_seen,
            "blank_rows_skipped": blank_rows_skipped,
            "batches_processed": batches_processed,
            "rows_with_findings": int(counters["rows_with_findings"]),
            "rows_with_hard_errors": int(counters["rows_with_hard_errors"]),
            "finding_count": sum(value for key, value in counters.items() if key.startswith("issue:")),
        }
        validation_runs.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    "status": "COMPLETED",
                    "completed_at": completed,
                    "summary": summary,
                }
            },
        )
        return {"run_id": run_id, "status": "COMPLETED", **summary}
    except Exception as exc:
        validation_runs.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    "status": "FAILED",
                    "failed_at": utc_now(),
                    "last_error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    "partial_rows_processed": rows_processed,
                    "partial_source_rows_seen": source_rows_seen,
                    "partial_blank_rows_skipped": blank_rows_skipped,
                    "partial_batches_processed": batches_processed,
                }
            },
        )
        raise
