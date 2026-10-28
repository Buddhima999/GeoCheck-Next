"""Streaming, bounded-batch CSV validation and MongoDB persistence.

The worker preflights CSV syntax before this processor writes any customer rows.
This function still streams the processing pass and never loads the whole file.
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
        "ADDRESS_LOCATION_MISMATCH",
        "ADDRESS_MISSING",
        "REPORTED_GEOGRAPHY_MISMATCH",
        "PHONE_AREA_MISMATCH",
        "DP_COORD_CONFLICT",
    }
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _search_terms_normalized(result: Mapping[str, Any]) -> list[str]:
    """Index normalized ID, phone and address terms for predictable-prefix searching."""
    source_values = result.get("source_values", {})
    if not isinstance(source_values, Mapping):
        source_values = {}
    values = (
        result.get("customer_id"),
        result.get("router_number"),
        result.get("dp_id"),
        source_values.get("router_number"),
        source_values.get("phone_number"),
        source_values.get("address"),
    )
    terms: set[str] = set()
    for value in values:
        text = str(value or "").casefold().strip()
        if not text:
            continue
        compact = "".join(character for character in text if character.isalnum())
        if compact:
            terms.add(compact)
        token: list[str] = []
        for character in text:
            if character.isalnum():
                token.append(character)
            elif token:
                terms.add("".join(token))
                token = []
        if token:
            terms.add("".join(token))
    return sorted(terms)


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


def _mark_customer_id_conflicts(
    *,
    run_id: str,
    customer_records: Collection,
    batch_size: int = 500,
) -> dict[str, int]:
    """Mark source rows whose normalized customer ID has conflicting signatures.

    MongoDB performs cross-row grouping with disk spill enabled. The cursor and update
    batches stay bounded so this works with large imports without loading all rows
    into Python memory. In-memory collection fakes skip this; live MongoDB tests cover it.
    """
    if not hasattr(customer_records, "aggregate"):
        return {"affected_rows": 0, "rows_newly_with_findings": 0}
    if batch_size < 1:
        raise ValueError("customer conflict batch_size must be at least 1")

    pipeline = [
        {"$match": {"run_id": run_id, "customer_id_normalized": {"$ne": None}}},
        {
            "$group": {
                "_id": {
                    "customer_id_normalized": "$customer_id_normalized",
                    "latitude": {"$round": ["$customer_location.lat", 5]},
                    "longitude": {"$round": ["$customer_location.lon", 5]},
                    "address": {
                        "$toLower": {
                            "$trim": {"input": {"$ifNull": ["$source_values.address", ""]}}
                        }
                    },
                    "phone": {"$ifNull": ["$phone_normalized", ""]},
                },
                "signature_rows": {"$sum": 1},
            }
        },
        {
            "$group": {
                "_id": "$_id.customer_id_normalized",
                "distinct_signatures": {"$sum": 1},
            }
        },
        {"$match": {"distinct_signatures": {"$gt": 1}}},
        {"$project": {"_id": 0, "customer_id_normalized": "$_id"}},
    ]
    cursor = customer_records.aggregate(pipeline, allowDiskUse=True).batch_size(batch_size)
    conflict_ids: list[str] = []
    affected_rows = 0
    newly_finding_rows = 0
    finding = {
        "code": "CUSTOMER_ID_CONFLICT",
        "status": FAIL,
        "severity": "warning",
        "message": "Customer ID occurs on multiple rows with different coordinates or identifying details.",
    }

    def apply_batch(ids: list[str]) -> None:
        nonlocal affected_rows, newly_finding_rows
        if not ids:
            return
        base_query = {"run_id": run_id, "customer_id_normalized": {"$in": ids}}
        newly_finding_rows += customer_records.count_documents(
            {**base_query, "issue_codes": []}
        )
        result = customer_records.update_many(
            {**base_query, "issue_codes": {"$ne": "CUSTOMER_ID_CONFLICT"}},
            {
                "$addToSet": {
                    "issue_codes": "CUSTOMER_ID_CONFLICT",
                    "findings": finding,
                },
                "$set": {"overall_status": FAIL, "has_fault": True},
            },
        )
        affected_rows += int(getattr(result, "modified_count", 0))

    for item in cursor:
        normalized_id = item.get("customer_id_normalized")
        if normalized_id:
            conflict_ids.append(str(normalized_id))
        if len(conflict_ids) >= batch_size:
            apply_batch(conflict_ids)
            conflict_ids = []
    apply_batch(conflict_ids)

    return {
        "affected_rows": affected_rows,
        "rows_newly_with_findings": newly_finding_rows,
    }




def _mark_dp_conflicted_rows(
    *,
    run_id: str,
    customer_records: Collection,
    dp_records: Collection,
    batch_size: int = 500,
) -> dict[str, int]:
    """Attach a DP coordinate-conflict finding to its affected customer rows.

    A DP may become conflicted only after a later processing batch is seen, so this
    bounded post-pass ensures the earliest customer rows are included in the report.
    All rows linked to the conflicted DP are marked because its reference location is
    ambiguous; they are not used as evidence for DP distance-quality assessment.
    """
    if not hasattr(dp_records, "find") or not hasattr(customer_records, "update_many"):
        return {"affected_rows": 0, "rows_newly_with_findings": 0}
    if batch_size < 1:
        raise ValueError("DP conflict batch_size must be at least 1")

    cursor = dp_records.find(
        {"run_id": run_id, "coordinate_consistency": "CONFLICT"},
        {"_id": 0, "dp_id_normalized": 1},
    ).batch_size(batch_size)
    conflict_ids: list[str] = []
    affected_rows = 0
    newly_finding_rows = 0
    finding = {
        "code": "DP_COORD_CONFLICT",
        "status": FAIL,
        "severity": "warning",
        "message": "This customer's DP ID has inconsistent coordinate observations; the DP reference location needs review.",
    }

    def apply_batch(ids: list[str]) -> None:
        nonlocal affected_rows, newly_finding_rows
        valid_ids = [value for value in ids if value]
        if not valid_ids:
            return
        query = {
            "run_id": run_id,
            "dp_id_normalized": {"$in": valid_ids},
            "issue_codes": {"$ne": "DP_COORD_CONFLICT"},
        }
        newly_finding_rows += customer_records.count_documents({
            "run_id": run_id,
            "dp_id_normalized": {"$in": valid_ids},
            "issue_codes": [],
        })
        result = customer_records.update_many(
            query,
            {
                "$addToSet": {
                    "issue_codes": "DP_COORD_CONFLICT",
                    "findings": finding,
                },
                "$set": {
                    "overall_status": FAIL,
                    "has_fault": True,
                    "eligible_for_dp_assessment": False,
                },
            },
        )
        affected_rows += int(getattr(result, "modified_count", 0))

    for item in cursor:
        normalized_id = item.get("dp_id_normalized")
        if normalized_id:
            conflict_ids.append(str(normalized_id))
        if len(conflict_ids) >= batch_size:
            apply_batch(conflict_ids)
            conflict_ids = []
    apply_batch(conflict_ids)
    return {"affected_rows": affected_rows, "rows_newly_with_findings": newly_finding_rows}



def _iter_dp_customer_counts(customer_records: Collection, run_id: str, batch_size: int):
    """Yield distinct customer counts per DP, using MongoDB aggregation in production.

    A normalized customer ID is counted once per DP. Rows without an ID use their
    source-row number as a conservative unique key so missing IDs cannot silently
    lower the port-use count. The in-memory path exists only for small test fakes.
    """
    if hasattr(customer_records, "aggregate"):
        pipeline = [
            {
                "$match": {
                    "run_id": run_id,
                    "dp_id_normalized": {"$nin": [None, ""]},
                }
            },
            {
                "$project": {
                    "dp_id_normalized": 1,
                    "dp_id": 1,
                    "_capacity_customer_key": {
                        "$cond": [
                            {"$eq": [{"$ifNull": ["$customer_id_normalized", ""]}, ""]},
                            {
                                "$concat": [
                                    "__missing_customer_row__",
                                    {
                                        "$toString": {
                                            "$ifNull": ["$row_number", "$_id"]
                                        }
                                    },
                                ]
                            },
                            "$customer_id_normalized",
                        ]
                    },
                }
            },
            {
                "$group": {
                    "_id": {
                        "dp_id_normalized": "$dp_id_normalized",
                        "customer_key": "$_capacity_customer_key",
                    },
                    "dp_id": {"$first": "$dp_id"},
                }
            },
            {
                "$group": {
                    "_id": "$_id.dp_id_normalized",
                    "dp_id": {"$first": "$dp_id"},
                    "distinct_customer_count": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        return customer_records.aggregate(pipeline, allowDiskUse=True).batch_size(batch_size)

    grouped: dict[str, dict[str, Any]] = {}
    for row in customer_records.find({"run_id": run_id}):
        normalized_dp = str(row.get("dp_id_normalized") or "").strip()
        if not normalized_dp:
            continue
        group = grouped.setdefault(
            normalized_dp,
            {"dp_id": row.get("dp_id"), "customer_keys": set()},
        )
        customer_id = str(row.get("customer_id_normalized") or "").strip()
        if customer_id:
            key = customer_id
        else:
            row_key = row.get("row_number", row.get("_id", len(group["customer_keys"])))
            key = f"__missing_customer_row__{row_key}"
        group["customer_keys"].add(key)

    return [
        {
            "_id": normalized_dp,
            "dp_id": group["dp_id"],
            "distinct_customer_count": len(group["customer_keys"]),
        }
        for normalized_dp, group in sorted(grouped.items())
    ]


def _mark_dp_port_capacity_violations(
    *,
    run_id: str,
    customer_records: Collection,
    dp_records: Collection,
    port_capacity: int = 8,
    batch_size: int = 500,
) -> dict[str, int]:
    """Record per-run DP port usage and flag DPs assigned to too many customers."""
    if port_capacity < 1:
        raise ValueError("DP port capacity must be at least 1")
    if batch_size < 1:
        raise ValueError("DP capacity batch_size must be at least 1")
    if not (
        hasattr(customer_records, "find")
        and hasattr(customer_records, "update_many")
        and hasattr(dp_records, "bulk_write")
    ):
        return {
            "affected_rows": 0,
            "rows_newly_with_findings": 0,
            "exceeded_dp_count": 0,
            "excess_customer_count": 0,
        }

    finding_code = "DP_PORT_CAPACITY_EXCEEDED"
    finding = {
        "code": finding_code,
        "status": FAIL,
        "severity": "error",
        "message": (
            f"This DP is assigned to more distinct customers than its configured "
            f"capacity of {port_capacity} ports."
        ),
    }
    affected_rows = 0
    rows_newly_with_findings = 0
    exceeded_dp_count = 0
    excess_customer_count = 0

    def apply_batch(items: list[dict[str, Any]]) -> None:
        nonlocal affected_rows, rows_newly_with_findings
        nonlocal exceeded_dp_count, excess_customer_count
        operations: list[UpdateOne] = []
        overloaded_dp_ids: list[str] = []
        for item in items:
            normalized_dp = str(item.get("_id") or "").strip()
            if not normalized_dp:
                continue
            try:
                assigned_count = int(item.get("distinct_customer_count", 0))
            except (TypeError, ValueError):
                continue
            over_capacity = assigned_count > port_capacity
            update: dict[str, Any] = {
                "$set": {
                    "port_capacity": port_capacity,
                    "assigned_customer_count": assigned_count,
                    "available_port_count": max(0, port_capacity - assigned_count),
                    "port_capacity_status": FAIL if over_capacity else PASS,
                    "port_capacity_exceeded_by": max(0, assigned_count - port_capacity),
                    "port_capacity_checked_at": utc_now(),
                }
            }
            if over_capacity:
                update["$addToSet"] = {"issue_codes": finding_code}
                overloaded_dp_ids.append(normalized_dp)
                exceeded_dp_count += 1
                excess_customer_count += assigned_count - port_capacity
            else:
                update["$pull"] = {"issue_codes": finding_code}
            operations.append(
                UpdateOne(
                    {"run_id": run_id, "dp_id_normalized": normalized_dp},
                    update,
                )
            )

        if operations:
            dp_records.bulk_write(operations, ordered=False)
        if not overloaded_dp_ids:
            return

        base_query = {
            "run_id": run_id,
            "dp_id_normalized": {"$in": overloaded_dp_ids},
        }
        rows_newly_with_findings += customer_records.count_documents(
            {**base_query, "issue_codes": []}
        )
        update_result = customer_records.update_many(
            {**base_query, "issue_codes": {"$ne": finding_code}},
            {
                "$addToSet": {
                    "issue_codes": finding_code,
                    "findings": finding,
                },
                "$set": {
                    "overall_status": FAIL,
                    "has_fault": True,
                    "dp_port_capacity_status": FAIL,
                    "dp_port_capacity": port_capacity,
                },
            },
        )
        affected_rows += int(getattr(update_result, "modified_count", 0))

    pending: list[dict[str, Any]] = []
    for item in _iter_dp_customer_counts(customer_records, run_id, batch_size):
        pending.append(item)
        if len(pending) >= batch_size:
            apply_batch(pending)
            pending = []
    if pending:
        apply_batch(pending)

    return {
        "affected_rows": affected_rows,
        "rows_newly_with_findings": rows_newly_with_findings,
        "exceeded_dp_count": exceeded_dp_count,
        "excess_customer_count": excess_customer_count,
    }


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
                "eligible_distance_assessed_rows": 0,
                "eligible_distance_pass_rows": 0,
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
            if isinstance(distance_check, Mapping):
                distance_status = distance_check.get("status")
                if distance_status in {PASS, FAIL}:
                    group["eligible_distance_assessed_rows"] += 1
                    if distance_status == PASS:
                        group["eligible_distance_pass_rows"] += 1
                    else:
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
            "eligible_distance_assessed_row_count": group.get("eligible_distance_assessed_rows", 0),
            "eligible_distance_pass_row_count": group.get("eligible_distance_pass_rows", 0),
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
            "eligible_distance_assessed_row_count": group.get("eligible_distance_assessed_rows", 0),
            "eligible_distance_pass_row_count": group.get("eligible_distance_pass_rows", 0),
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
    max_rows: int | None = None,
    settings: ValidationSettings = ValidationSettings(),
    dp_port_capacity: int = 8,
    country_check: Callable[[float, float], bool | None] | None = None,
    phone_area_check: Callable[[str, float, float], bool | None] | None = None,
    address_zone_check: Callable[[str, float, float], bool | None] | None = None,
    geography_check: Callable[[Mapping[str, Any], float, float], bool | None] | None = None,
    route_distance_check: Callable[[float, float, float, float], float | None] | None = None,
    geocode_address_check: Callable[[str, float, float], float | None] | None = None,
) -> dict[str, Any]:
    """Validate and persist a CSV in bounded batches.

    The caller must create the validation_runs document first. A worker should
    preflight the entire CSV before calling this service. Queueing and restart
    recovery are handled separately; partial writes are not safe to auto-resume.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if max_rows is not None and max_rows < 1:
        raise ValueError("max_rows must be at least 1 when configured")
    if dp_port_capacity < 1:
        raise ValueError("dp_port_capacity must be at least 1")

    start_time = utc_now()
    validation_runs.update_one(
        {"run_id": run_id},
        {
            "$set": {
                "status": "PROCESSING",
                "stage": "VALIDATING",
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
            mapped_rows = iter_mapped_rows(
                stream,
                mapping,
                include_blank_rows=True,
                max_rows=max_rows,
            )
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
                        route_distance_check=route_distance_check,
                        geocode_address_check=geocode_address_check,
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
                            "router_number": result.get("router_number"),
                            "calculated_customer_dp_distance_m": result.get("calculated_customer_dp_distance_m"),
                            "overhead_distance_m": result.get("overhead_distance_m"),
                            "overhead_distance_500m_status": result.get("overhead_distance_500m_status"),
                            "google_route_distance_m": result.get("google_route_distance_m"),
                            "distance_500m_m": result.get("distance_500m_m"),
                            "distance_500m_status": result.get("distance_500m_status"),
                            "distance_method": result.get("distance_method"),
                            "customer_inside_sri_lanka": result.get("customer_inside_sri_lanka"),
                            "overall_status": result.get("overall_status", "UNVERIFIED"),
                            "has_fault": result.get("has_fault", False),
                            "source_row_values": result.get("source_row_values", {}),
                            "dp_id": result.get("dp_id"),
                            "dp_id_normalized": result.get("dp_id_normalized"),
                            "source_values": result.get("source_values", {}),
                            "phone_normalized": "".join(
                                character
                                for character in str(
                                    (result.get("source_values", {}) or {}).get("phone_number")
                                    or (result.get("source_values", {}) or {}).get("router_number")
                                    or ""
                                )
                                if character.isdigit()
                            ),
                            "search_terms_normalized": _search_terms_normalized(result),
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

        customer_conflicts = _mark_customer_id_conflicts(
            run_id=run_id,
            customer_records=customer_records,
        )
        conflict_rows = customer_conflicts["affected_rows"]
        if conflict_rows:
            counters["issue:CUSTOMER_ID_CONFLICT"] += conflict_rows
            counters["rows_with_findings"] += customer_conflicts["rows_newly_with_findings"]
            validation_runs.update_one(
                {"run_id": run_id},
                {
                    "$inc": {
                        "issue_counts.CUSTOMER_ID_CONFLICT": conflict_rows,
                        "rows_with_findings": customer_conflicts["rows_newly_with_findings"],
                    },
                    "$set": {"last_progress_at": utc_now()},
                },
            )

        dp_conflicts = _mark_dp_conflicted_rows(
            run_id=run_id,
            customer_records=customer_records,
            dp_records=dp_records,
        )
        dp_conflict_rows = dp_conflicts["affected_rows"]
        if dp_conflict_rows:
            counters["issue:DP_COORD_CONFLICT"] += dp_conflict_rows
            counters["rows_with_findings"] += dp_conflicts["rows_newly_with_findings"]
            validation_runs.update_one(
                {"run_id": run_id},
                {
                    "$inc": {
                        "issue_counts.DP_COORD_CONFLICT": dp_conflict_rows,
                        "rows_with_findings": dp_conflicts["rows_newly_with_findings"],
                    },
                    "$set": {"last_progress_at": utc_now()},
                },
            )

        dp_capacity = _mark_dp_port_capacity_violations(
            run_id=run_id,
            customer_records=customer_records,
            dp_records=dp_records,
            port_capacity=dp_port_capacity,
            batch_size=min(batch_size, 500),
        )
        capacity_rows = dp_capacity["affected_rows"]
        if capacity_rows:
            counters["issue:DP_PORT_CAPACITY_EXCEEDED"] += capacity_rows
            counters["rows_with_findings"] += dp_capacity["rows_newly_with_findings"]
            validation_runs.update_one(
                {"run_id": run_id},
                {
                    "$inc": {
                        "issue_counts.DP_PORT_CAPACITY_EXCEEDED": capacity_rows,
                        "rows_with_findings": dp_capacity["rows_newly_with_findings"],
                    },
                    "$set": {"last_progress_at": utc_now()},
                },
            )

        completed = utc_now()
        summary = {
            "rows_processed": rows_processed,
            "source_rows_seen": source_rows_seen,
            "blank_rows_skipped": blank_rows_skipped,
            "batches_processed": batches_processed,
            "dp_port_capacity": dp_port_capacity,
            "dp_port_capacity_exceeded_count": dp_capacity["exceeded_dp_count"],
            "dp_port_capacity_exceeded_rows": dp_capacity["affected_rows"],
            "dp_port_capacity_excess_customer_count": dp_capacity["excess_customer_count"],
            "rows_with_findings": int(counters["rows_with_findings"]),
            "rows_with_hard_errors": int(counters["rows_with_hard_errors"]),
            "finding_count": sum(value for key, value in counters.items() if key.startswith("issue:")),
        }
        validation_runs.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    "status": "COMPLETED",
                    "stage": "COMPLETED",
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
                    "stage": "FAILED",
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
