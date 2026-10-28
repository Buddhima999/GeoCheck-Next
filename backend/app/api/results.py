"""Indexed, paginated queries and streaming exports for completed validation runs."""
from __future__ import annotations

import csv
import io
import math
import re
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pymongo.collection import Collection

from ..database import customer_records, dp_records, validation_runs
from ..validation.csv_import import normalize_identifier
from .validation_runs import require_api_key

router = APIRouter(prefix="/api/validation-runs", tags=["validation-results"])
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
MAX_QUERY_LENGTH = 100


def _get_run_or_404(run_id: str) -> dict[str, Any]:
    run = validation_runs.find_one(
        {"run_id": run_id},
        {
            "_id": 0,
            "run_id": 1,
            "status": 1,
            "summary": 1,
            "issue_counts": 1,
            "original_filename": 1,
            "created_at": 1,
        },
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Validation run not found.")
    return run


def _customer_search_query(query: str | None) -> dict[str, Any] | None:
    """Search pre-normalized row terms using indexed prefix expressions."""
    if query is None or not query.strip():
        return None
    needle = query.strip()
    if len(needle) > MAX_QUERY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Search text must be at most {MAX_QUERY_LENGTH} characters.",
        )
    compact = "".join(character for character in needle.casefold() if character.isalnum())
    tokens: list[str] = []
    current: list[str] = []
    for character in needle.casefold():
        if character.isalnum():
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    if not compact:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Search text must contain at least one letter or number.",
        )

    compact_prefix = {"search_terms_normalized": {"$regex": "^" + re.escape(compact)}}
    if len(tokens) <= 1:
        return compact_prefix
    token_query = {
        "$and": [
            {"search_terms_normalized": {"$regex": "^" + re.escape(token)}}
            for token in tokens
        ]
    }
    return {"$or": [compact_prefix, token_query]}


def _dp_search_query(query: str | None) -> dict[str, Any] | None:
    """Use the indexed normalized DP identifier for predictable-prefix searches."""
    if query is None or not query.strip():
        return None
    needle = query.strip()
    if len(needle) > MAX_QUERY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Search text must be at most {MAX_QUERY_LENGTH} characters.",
        )
    normalized = needle.casefold()
    return {"dp_id_normalized": {"$regex": "^" + re.escape(normalized)}}


def _and_queries(*queries: dict[str, Any] | None) -> dict[str, Any]:
    active = [query for query in queries if query]
    if not active:
        return {}
    if len(active) == 1:
        return active[0]
    return {"$and": active}


def _reported_geography_query(
    province: str | None = None,
    district: str | None = None,
    region: str | None = None,
) -> dict[str, Any] | None:
    """Filter by resolved geography labels, falling back to exact CSV labels."""
    parts: list[dict[str, Any] | None] = []
    for key, value in (
        ("reported_province", province),
        ("reported_district", district),
        ("reported_region", region),
    ):
        if value is None or not value.strip():
            continue
        field = key.removeprefix("reported_")
        id_path = f"geography.{field}_id"
        source_path = f"source_values.{key}"
        if value.strip().upper() == "UNREPORTED":
            source_fallback = {"$and": [
                {id_path: {"$exists": False}},
                {"$or": [{source_path: {"$exists": False}}, {source_path: None}, {source_path: ""}]},
            ]}
            parts.append({"$or": [{id_path: "unreported"}, source_fallback]})
        else:
            source_fallback = {"$and": [
                {id_path: {"$exists": False}},
                {source_path: value.strip()},
            ]}
            parts.append({"$or": [{id_path: value.strip().casefold()}, source_fallback]})
    return _and_queries(*parts)


def _strip_mongo_id(document: dict[str, Any]) -> dict[str, Any]:
    document.pop("_id", None)
    return document


@router.get("", dependencies=[Depends(require_api_key)])
def list_validation_runs(
    limit: int = Query(default=20, ge=1, le=100),
    skip: int = Query(default=0, ge=0, le=100_000),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
) -> dict[str, Any]:
    """List recent runs without exposing internal file paths or worker data."""
    query: dict[str, Any] = {}
    if status_filter:
        allowed = {"QUEUED", "PROCESSING", "COMPLETED", "FAILED"}
        normalized = status_filter.upper()
        if normalized not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"status must be one of: {', '.join(sorted(allowed))}.",
            )
        query["status"] = normalized

    total = validation_runs.count_documents(query)
    cursor = validation_runs.find(
        query,
        {
            "_id": 0,
            "run_id": 1,
            "status": 1,
            "stage": 1,
            "original_filename": 1,
            "file_size_bytes": 1,
            "created_at": 1,
            "processing_started_at": 1,
            "completed_at": 1,
            "failed_at": 1,
            "rows_processed": 1,
            "source_rows_seen": 1,
            "blank_rows_skipped": 1,
            "batches_processed": 1,
            "rows_with_findings": 1,
            "rows_with_hard_errors": 1,
            "summary": 1,
        },
    ).sort("created_at", -1).skip(skip).limit(limit)
    return {"items": list(cursor), "total": total, "limit": limit, "skip": skip}


@router.get("/{run_id}/summary", dependencies=[Depends(require_api_key)])
def get_run_summary(run_id: str) -> dict[str, Any]:
    """Return consistent source, customer-result and DP-record counts for one run."""
    run = _get_run_or_404(run_id)
    customers: Collection = customer_records
    dps: Collection = dp_records
    customer_total = customers.count_documents({"run_id": run_id})
    customer_with_findings = customers.count_documents(
        {"run_id": run_id, "issue_codes.0": {"$exists": True}}
    )
    customer_hard_errors = customers.count_documents({"run_id": run_id, "has_hard_error": True})
    eligible_customers = customers.count_documents(
        {"run_id": run_id, "eligible_for_dp_assessment": True}
    )
    faulty_customers = customers.count_documents({"run_id": run_id, "overall_status": "FAIL"})
    unverified_customers = customers.count_documents({"run_id": run_id, "overall_status": "UNVERIFIED"})
    passed_customers = customers.count_documents({"run_id": run_id, "overall_status": "PASS"})
    dp_total = dps.count_documents({"run_id": run_id})
    dp_consistent = dps.count_documents({"run_id": run_id, "coordinate_consistency": "CONSISTENT"})
    dp_conflict = dps.count_documents({"run_id": run_id, "coordinate_consistency": "CONFLICT"})
    dp_unverified = dps.count_documents({"run_id": run_id, "coordinate_consistency": "UNVERIFIED"})
    dp_port_capacity_exceeded = dps.count_documents({"run_id": run_id, "port_capacity_status": "FAIL"})
    correct_csv_rows = customers.count_documents({"run_id": run_id, "overall_status": {"$ne": "FAIL"}})
    correct_customer_locations = customers.count_documents({
        "run_id": run_id,
        "$or": [
            {"customer_location_has_error": False},
            {"customer_location_has_error": {"$exists": False}, "overall_status": {"$ne": "FAIL"}},
        ],
    })
    correct_dp_locations = dps.count_documents({"run_id": run_id, "dp_assessment_status": "PASS"})

    summary = run.get("summary") or {}
    return {
        "run_id": run_id,
        "status": run.get("status"),
        "original_filename": run.get("original_filename"),
        "created_at": run.get("created_at"),
        "source_rows_seen": run.get("source_rows_seen", summary.get("source_rows_seen", 0)),
        "blank_rows_skipped": run.get("blank_rows_skipped", summary.get("blank_rows_skipped", 0)),
        "customer_rows_stored": customer_total,
        "rows_with_findings": customer_with_findings,
        "rows_with_hard_errors": customer_hard_errors,
        "faulty_customers": faulty_customers,
        "unverified_customers": unverified_customers,
        "passed_customers": passed_customers,
        "eligible_for_dp_assessment": eligible_customers,
        "dp_count": dp_total,
        "dp_coordinate_consistent": dp_consistent,
        "dp_coordinate_conflict": dp_conflict,
        "dp_coordinate_unverified": dp_unverified,
        "dp_port_capacity_exceeded": dp_port_capacity_exceeded,
        "dp_port_capacity_limit": int(summary.get("dp_port_capacity", 8) or 8),
        "correct_csv_rows": correct_csv_rows,
        "correct_csv_rows_percentage": round(correct_csv_rows * 100.0 / customer_total, 2) if customer_total else None,
        "correct_customer_locations": correct_customer_locations,
        "correct_customer_locations_percentage": round(correct_customer_locations * 100.0 / customer_total, 2) if customer_total else None,
        "correct_dp_locations": correct_dp_locations,
        "correct_dp_locations_percentage": round(correct_dp_locations * 100.0 / dp_total, 2) if dp_total else None,
        "dp_assessment_fail": dps.count_documents({"run_id": run_id, "dp_assessment_status": "FAIL"}),
        "dp_assessment_unverified": dps.count_documents({"run_id": run_id, "dp_assessment_status": "UNVERIFIED"}),
        "customer_dp_limit_m": float(summary.get("customer_dp_limit_m", 500.0)),
        "issue_counts": run.get("issue_counts", {}),
        "processor_summary": summary,
        "note": "Reference-dependent checks remain UNVERIFIED until approved reference datasets are configured.",
    }


@router.get("/{run_id}/customers", dependencies=[Depends(require_api_key)])
def list_customer_results(
    run_id: str,
    q: str | None = Query(default=None, max_length=MAX_QUERY_LENGTH),
    issue_code: str | None = Query(default=None, max_length=80),
    eligible_for_dp_assessment: bool | None = Query(default=None),
    province: str | None = Query(default=None, max_length=120),
    district: str | None = Query(default=None, max_length=120),
    region: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    skip: int = Query(default=0, ge=0, le=1_000_000),
) -> dict[str, Any]:
    """Search indexed customer IDs and bounded page windows of customer outcomes."""
    _get_run_or_404(run_id)
    query = _and_queries(
        {"run_id": run_id},
        _customer_search_query(q),
        {"issue_codes": issue_code.strip().upper()} if issue_code and issue_code.strip() else None,
        {"eligible_for_dp_assessment": eligible_for_dp_assessment}
        if eligible_for_dp_assessment is not None else None,
        _reported_geography_query(province, district, region),
    )
    total = customer_records.count_documents(query)
    cursor = customer_records.find(
        query,
        {
            "_id": 0,
            "run_id": 1,
            "row_number": 1,
            "customer_id": 1,
            "customer_id_normalized": 1,
            "dp_id": 1,
            "dp_id_normalized": 1,
            "source_values": 1,
            "source_row_values": 1,
            "router_number": 1,
            "calculated_customer_dp_distance_m": 1,
            "overhead_distance_m": 1,
            "overhead_distance_500m_status": 1,
            "google_route_distance_m": 1,
            "distance_500m_m": 1,
            "distance_500m_status": 1,
            "distance_method": 1,
            "customer_inside_sri_lanka": 1,
            "overall_status": 1,
            "has_fault": 1,
            "dp_port_capacity_status": 1,
            "dp_port_capacity": 1,
            "customer_location": 1,
            "dp_location_observed": 1,
            "checks": 1,
            "issue_codes": 1,
            "findings": 1,
            "has_hard_error": 1,
            "eligible_for_dp_assessment": 1,
            "customer_location_has_error": 1,
        },
    ).sort("row_number", 1).skip(skip).limit(limit)
    return {"items": list(cursor), "total": total, "limit": limit, "skip": skip}


@router.get("/{run_id}/dps", dependencies=[Depends(require_api_key)])
def list_dp_results(
    run_id: str,
    q: str | None = Query(default=None, max_length=MAX_QUERY_LENGTH),
    consistency: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    skip: int = Query(default=0, ge=0, le=1_000_000),
) -> dict[str, Any]:
    """Search DP rollups without embedding their customer rows."""
    _get_run_or_404(run_id)
    query_parts: list[dict[str, Any] | None] = [
        {"run_id": run_id},
        _dp_search_query(q),
    ]
    if consistency:
        normalized = consistency.strip().upper()
        allowed = {"CONSISTENT", "CONFLICT", "UNVERIFIED"}
        if normalized not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"consistency must be one of: {', '.join(sorted(allowed))}.",
            )
        query_parts.append({"coordinate_consistency": normalized})
    query = _and_queries(*query_parts)
    total = dp_records.count_documents(query)
    cursor = dp_records.find(
        query,
        {
            "_id": 0,
            "run_id": 1,
            "dp_id": 1,
            "dp_id_normalized": 1,
            "dp_location_observed": 1,
            "coordinate_consistency": 1,
            "country_boundary_status": 1,
            "dp_assessment_status": 1,
            "dp_association_min_pass_fraction": 1,
            "observed_row_count": 1,
            "valid_coordinate_observation_count": 1,
            "coordinate_conflict_observation_count": 1,
            "eligible_customer_row_count": 1,
            "eligible_distance_assessed_row_count": 1,
            "eligible_distance_pass_row_count": 1,
            "eligible_distance_fail_row_count": 1,
            "association_assessed_row_count": 1,
            "association_pass_row_count": 1,
            "association_fail_row_count": 1,
            "port_capacity": 1,
            "assigned_customer_count": 1,
            "available_port_count": 1,
            "port_capacity_status": 1,
            "port_capacity_exceeded_by": 1,
            "issue_codes": 1,
        },
    ).sort("dp_id_normalized", 1).skip(skip).limit(limit)
    items = []
    for document in cursor:
        item = dict(document)
        eligible_rows = int(item.get("eligible_customer_row_count", 0) or 0)
        assessed_rows = int(item.get("eligible_distance_assessed_row_count", 0) or 0)
        failed_rows = int(item.get("eligible_distance_fail_row_count", 0) or 0)
        passed_rows = int(item.get("eligible_distance_pass_row_count", 0) or 0)
        association_assessed = int(item.get("association_assessed_row_count", 0) or 0)
        association_failed = int(item.get("association_fail_row_count", 0) or 0)
        association_passed = int(item.get("association_pass_row_count", 0) or 0)
        item["eligible_distance_assessed_row_count"] = assessed_rows
        item["eligible_distance_pass_row_count"] = passed_rows
        item["eligible_distance_fail_percentage"] = (
            round(failed_rows * 100.0 / assessed_rows, 2) if assessed_rows else None
        )
        item["association_assessed_row_count"] = association_assessed
        item["association_pass_row_count"] = association_passed
        item["association_fail_row_count"] = association_failed
        item["association_fail_percentage"] = round(association_failed * 100.0 / association_assessed, 2) if association_assessed else None
        if association_assessed == 0:
            item["association_evidence"] = "INSUFFICIENT_ELIGIBLE_ROWS"
        elif association_failed > 0:
            item["association_evidence"] = "ASSOCIATION_FAILURES_OBSERVED"
        else:
            item["association_evidence"] = "NO_ASSOCIATION_FAILURES_OBSERVED"
        item["association_threshold_configured"] = True
        items.append(item)
    return {"items": items, "total": total, "limit": limit, "skip": skip}


@router.get("/{run_id}/dps/{dp_id}/customers", dependencies=[Depends(require_api_key)])
def list_dp_customer_map_points(
    run_id: str,
    dp_id: str,
    limit: int = Query(default=1000, ge=1, le=1000),
) -> dict[str, Any]:
    """Return a bounded set of valid customer coordinates assigned to one FDP."""
    _get_run_or_404(run_id)
    normalized_dp_id = normalize_identifier(dp_id)
    if not normalized_dp_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="FDP ID is required.")

    assigned_query = {"run_id": run_id, "dp_id_normalized": normalized_dp_id}
    mapped_query = {
        **assigned_query,
        "customer_location.lat": {"$exists": True},
        "customer_location.lon": {"$exists": True},
    }
    assigned_rows = customer_records.count_documents(assigned_query)
    mapped_rows = customer_records.count_documents(mapped_query)
    cursor = customer_records.find(
        mapped_query,
        {
            "_id": 0,
            "row_number": 1,
            "customer_id": 1,
            "customer_id_normalized": 1,
            "customer_location": 1,
            "overall_status": 1,
            "source_values.address": 1,
        },
    ).sort("row_number", 1).limit(limit)

    points = []
    seen_customers: set[str] = set()
    for document in cursor:
        location = document.get("customer_location") or {}
        lat, lon = location.get("lat"), location.get("lon")
        if (
            not isinstance(lat, (int, float)) or isinstance(lat, bool)
            or not isinstance(lon, (int, float)) or isinstance(lon, bool)
            or not math.isfinite(lat) or not math.isfinite(lon)
            or abs(lat) > 90 or abs(lon) > 180
        ):
            continue
        customer_id = document.get("customer_id")
        row_number = document.get("row_number")
        identity = str(document.get("customer_id_normalized") or f"row:{row_number}")
        if identity in seen_customers:
            continue
        seen_customers.add(identity)
        points.append({
            "customer_id": customer_id,
            "row_number": row_number,
            "latitude": lat,
            "longitude": lon,
            "address": (document.get("source_values") or {}).get("address"),
            "status": document.get("overall_status", "UNVERIFIED"),
        })

    return {
        "dp_id": dp_id,
        "assigned_rows": assigned_rows,
        "mapped_rows": mapped_rows,
        "returned_points": len(points),
        "truncated": mapped_rows > limit,
        "customers": points,
    }


@router.get("/{run_id}/hierarchy", dependencies=[Depends(require_api_key)])
def get_geographic_hierarchy(
    run_id: str,
    province: str | None = Query(default=None, max_length=120),
    district: str | None = Query(default=None, max_length=120),
    region: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=200, ge=1, le=1000),
    skip: int = Query(default=0, ge=0, le=10_000),
) -> dict[str, Any]:
    """Group source-reported province/district/region labels without guessing mappings."""
    _get_run_or_404(run_id)
    geography_match = _and_queries({"run_id": run_id}, _reported_geography_query(province, district, region)) or {"run_id": run_id}
    area_summary_cursor = customer_records.aggregate([
        {"$match": geography_match},
        {"$group": {
            "_id": None,
            "customer_rows": {"$sum": 1},
            "rows_with_findings": {"$sum": {"$cond": [
                {"$gt": [{"$size": {"$ifNull": ["$issue_codes", []]}}, 0]}, 1, 0,
            ]}},
        }},
    ], allowDiskUse=True)
    area_summary_rows = list(area_summary_cursor)
    area_summary = area_summary_rows[0] if area_summary_rows else {"customer_rows": 0, "rows_with_findings": 0}
    pipeline = [
        {"$match": geography_match},
        {
            "$group": {
                "_id": {
                    "province": {"$cond": [{"$eq": [{"$ifNull": ["$geography.province_label", {"$ifNull": ["$source_values.reported_province", ""]}]}, ""]}, "UNREPORTED", {"$ifNull": ["$geography.province_label", "$source_values.reported_province"]}]},
                    "district": {"$cond": [{"$eq": [{"$ifNull": ["$geography.district_label", {"$ifNull": ["$source_values.reported_district", ""]}]}, ""]}, "UNREPORTED", {"$ifNull": ["$geography.district_label", "$source_values.reported_district"]}]},
                    "region": {"$cond": [{"$eq": [{"$ifNull": ["$geography.region_label", {"$ifNull": ["$source_values.reported_region", ""]}]}, ""]}, "UNREPORTED", {"$ifNull": ["$geography.region_label", "$source_values.reported_region"]}]},
                    "dp_id": "$dp_id_normalized",
                },
                "row_count": {"$sum": 1},
                "rows_with_findings": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$size": {"$ifNull": ["$issue_codes", []]}}, 0]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$group": {
                "_id": {
                    "province": "$_id.province",
                    "district": "$_id.district",
                    "region": "$_id.region",
                },
                "customer_rows": {"$sum": "$row_count"},
                "rows_with_findings": {"$sum": "$rows_with_findings"},
                "dp_count": {"$sum": {"$cond": [{"$ne": ["$_id.dp_id", None]}, 1, 0]}},
            }
        },
        {"$sort": {"_id.province": 1, "_id.district": 1, "_id.region": 1}},
        {
            "$facet": {
                "items": [{"$skip": skip}, {"$limit": limit}],
                "metadata": [{"$count": "total"}],
            }
        },
    ]
    response = list(customer_records.aggregate(pipeline, allowDiskUse=True))
    payload = response[0] if response else {"items": [], "metadata": []}
    total = payload["metadata"][0]["total"] if payload.get("metadata") else 0
    items = [
        {
            "province": row["_id"].get("province") or "UNREPORTED",
            "district": row["_id"].get("district") or "UNREPORTED",
            "region": row["_id"].get("region") or "UNREPORTED",
            "customer_rows": row["customer_rows"],
            "rows_with_findings": row["rows_with_findings"],
            "dp_count": row["dp_count"],
        }
        for row in payload.get("items", [])
    ]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "skip": skip,
        "area_customer_rows": int(area_summary.get("customer_rows", 0)),
        "area_rows_with_findings": int(area_summary.get("rows_with_findings", 0)),
    }


@router.get("/{run_id}/hierarchy/options", dependencies=[Depends(require_api_key)])
def get_geographic_hierarchy_options(
    run_id: str,
    province: str | None = Query(default=None, max_length=120),
    district: str | None = Query(default=None, max_length=120),
) -> dict[str, list[str]]:
    """Return selector values from CSV-reported labels, narrowed by parent selection."""
    _get_run_or_404(run_id)
    base = {"run_id": run_id}
    province_filter = _reported_geography_query(province)
    district_filter = _reported_geography_query(province, district)

    def values_facet(field: str, match: dict[str, Any]) -> list[str]:
        label_field = field.removeprefix("reported_")
        expression = {"$ifNull": [f"$geography.{label_field}_label", {"$ifNull": [f"$source_values.{field}", ""]}]}
        pipeline = [
            {"$match": match},
            {"$group": {"_id": expression}},
            {"$project": {"value": {"$cond": [{"$eq": ["$_id", ""]}, "UNREPORTED", "$_id"]}}},
            {"$sort": {"value": 1}},
        ]
        return [str(item["value"]) for item in customer_records.aggregate(pipeline, allowDiskUse=True)]

    return {
        "provinces": values_facet("reported_province", base),
        "districts": values_facet("reported_district", _and_queries(base, province_filter) or base),
        "regions": values_facet("reported_region", _and_queries(base, district_filter) or base),
    }


@router.get("/{run_id}/hierarchy/dps", dependencies=[Depends(require_api_key)])
def list_hierarchy_dps(
    run_id: str,
    province: str | None = Query(default=None, max_length=120),
    district: str | None = Query(default=None, max_length=120),
    region: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    skip: int = Query(default=0, ge=0, le=1_000_000),
) -> dict[str, Any]:
    """Page DP rollups associated with customer rows in the selected reported area."""
    _get_run_or_404(run_id)
    match = _and_queries(
        {"run_id": run_id},
        _reported_geography_query(province, district, region),
        {"dp_id_normalized": {"$exists": True, "$nin": [None, ""]}},
    )
    pipeline = [
        {"$match": match},
        {"$group": {"_id": {
            "dp": "$dp_id_normalized",
            "customer": {"$cond": [
                {"$eq": [{"$ifNull": ["$customer_id_normalized", ""]}, ""]},
                {"$concat": ["__missing_customer__", {"$toString": {"$ifNull": ["$row_number", "$_id"]}}]},
                "$customer_id_normalized",
            ]},
        }, "geography_customer_rows": {"$sum": 1}}},
        {"$group": {"_id": "$_id.dp", "geography_customer_count": {"$sum": 1}, "geography_customer_rows": {"$sum": "$geography_customer_rows"}}},
        {"$sort": {"_id": 1}},
        {"$facet": {
            "items": [
                {"$skip": skip}, {"$limit": limit},
                {"$lookup": {
                    "from": dp_records.name,
                    "let": {"dp_key": "$_id"},
                    "pipeline": [{"$match": {"$expr": {"$and": [
                        {"$eq": ["$run_id", run_id]},
                        {"$eq": ["$dp_id_normalized", "$$dp_key"]},
                    ]}}}, {"$limit": 1}],
                    "as": "dp",
                }},
                {"$unwind": {"path": "$dp", "preserveNullAndEmptyArrays": False}},
                {"$replaceRoot": {"newRoot": {"$mergeObjects": ["$dp", {"geography_customer_count": "$geography_customer_count", "geography_customer_rows": "$geography_customer_rows"}]}}},
                {"$project": {"_id": 0}},
            ],
            "metadata": [{"$count": "total"}],
        }},
    ]
    response = list(customer_records.aggregate(pipeline, allowDiskUse=True))
    payload = response[0] if response else {"items": [], "metadata": []}
    total = payload["metadata"][0]["total"] if payload.get("metadata") else 0
    return {"items": payload.get("items", []), "total": total, "limit": limit, "skip": skip}


def _csv_cell(value: Any) -> str:
    """Protect spreadsheet applications from interpreting source text as formulas."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = str(value)
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + text
    return text


def _csv_stream(cursor: Iterator[dict[str, Any]], fields: list[str]) -> Iterator[str]:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(fields)
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)
    for document in cursor:
        writer.writerow([_csv_cell(document.get(field)) for field in fields])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)


@router.get("/{run_id}/customers/export.csv", dependencies=[Depends(require_api_key)])
def export_customer_results(
    run_id: str,
    issue_code: str | None = Query(default=None, max_length=80),
    report_type: str = Query(default="all", max_length=20),
) -> StreamingResponse:
    """Stream the full audit, confirmed faults only, or rows needing verification."""
    _get_run_or_404(run_id)
    if report_type not in {"all", "faults", "unverified"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="report_type must be all, faults or unverified.")
    query: dict[str, Any] = {"run_id": run_id}
    if report_type == "faults":
        query["overall_status"] = "FAIL"
    elif report_type == "unverified":
        query["overall_status"] = "UNVERIFIED"
    if issue_code and issue_code.strip():
        query["issue_codes"] = issue_code.strip().upper()
    cursor = customer_records.find(
        query,
        {
            "_id": 0,
            "row_number": 1,
            "customer_id": 1,
            "dp_id": 1,
            "source_values": 1,
            "source_row_values": 1,
            "router_number": 1,
            "calculated_customer_dp_distance_m": 1,
            "overhead_distance_m": 1,
            "overhead_distance_500m_status": 1,
            "google_route_distance_m": 1,
            "distance_500m_m": 1,
            "distance_500m_status": 1,
            "distance_method": 1,
            "customer_inside_sri_lanka": 1,
            "overall_status": 1,
            "dp_port_capacity_status": 1,
            "dp_port_capacity": 1,
            "checks": 1,
            "customer_location": 1,
            "dp_location_observed": 1,
            "issue_codes": 1,
            "findings": 1,
            "has_hard_error": 1,
        },
    ).sort("row_number", 1).batch_size(1000)
    fields = [
        "row_number",
        "customer_id",
        "router_number",
        "address",
        "customer_latitude",
        "customer_longitude",
        "dp_id",
        "dp_latitude",
        "dp_longitude",
        "line_distance_m",
        "calculated_customer_dp_distance_m",
        "overhead_distance_m",
        "overhead_distance_500m_status",
        "reported_overhead_distance_m",
        "google_route_distance_m",
        "distance_500m_m",
        "distance_method",
        "distance_500m_status",
        "line_distance_status",
        "customer_inside_sri_lanka",
        "address_match_status",
        "router_number_format_status",
        "router_number_area_code",
        "router_number_area",
        "phone_area_status",
        "phone_area_message",
        "overall_status",
        "dp_port_capacity_status",
        "dp_port_capacity",
        "source_row_values",
        "issue_codes",
        "findings",
        "has_hard_error",
    ]

    def mapped_rows() -> Iterator[dict[str, Any]]:
        for doc in cursor:
            source = doc.get("source_values") or {}
            yield {
                "row_number": doc.get("row_number"),
                "customer_id": doc.get("customer_id"),
                "router_number": doc.get("router_number") or source.get("router_number") or source.get("phone_number"),
                "address": source.get("address"),
                "customer_latitude": source.get("customer_latitude"),
                "customer_longitude": source.get("customer_longitude"),
                "dp_id": doc.get("dp_id"),
                "dp_latitude": source.get("dp_latitude"),
                "dp_longitude": source.get("dp_longitude"),
                "line_distance_m": source.get("line_distance_m"),
                "calculated_customer_dp_distance_m": doc.get("calculated_customer_dp_distance_m"),
                "overhead_distance_m": doc.get("overhead_distance_m"),
                "overhead_distance_500m_status": doc.get("overhead_distance_500m_status") or (doc.get("checks") or {}).get("overhead_distance_500m", {}).get("status"),
                "reported_overhead_distance_m": source.get("overhead_distance_m"),
                "google_route_distance_m": doc.get("google_route_distance_m"),
                "distance_500m_m": doc.get("distance_500m_m"),
                "distance_method": doc.get("distance_method"),
                "distance_500m_status": doc.get("distance_500m_status") or (doc.get("checks") or {}).get("customer_dp_distance", {}).get("status"),
                "line_distance_status": (doc.get("checks") or {}).get("line_distance", {}).get("status"),
                "customer_inside_sri_lanka": doc.get("customer_inside_sri_lanka"),
                "address_match_status": (doc.get("checks") or {}).get("address_zone", {}).get("status"),
                "router_number_format_status": (doc.get("checks") or {}).get("router_number_format", {}).get("status"),
                "router_number_area_code": (doc.get("checks") or {}).get("router_number_format", {}).get("area_code"),
                "router_number_area": (doc.get("checks") or {}).get("router_number_format", {}).get("area_name"),
                "phone_area_status": (doc.get("checks") or {}).get("phone_area", {}).get("status"),
                "phone_area_message": (doc.get("checks") or {}).get("phone_area", {}).get("message"),
                "overall_status": doc.get("overall_status", "UNVERIFIED"),
                "dp_port_capacity_status": doc.get("dp_port_capacity_status"),
                "dp_port_capacity": doc.get("dp_port_capacity"),
                "source_row_values": doc.get("source_row_values") or source,
                "issue_codes": doc.get("issue_codes"),
                "findings": doc.get("findings"),
                "has_hard_error": doc.get("has_hard_error"),
            }

    return StreamingResponse(
        _csv_stream(mapped_rows(), fields),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="geocheck-{run_id}-{report_type}-customers.csv"'},
    )


@router.get("/{run_id}/dps/export.csv", dependencies=[Depends(require_api_key)])
def export_dp_results(run_id: str, consistency: str | None = Query(default=None, max_length=20)) -> StreamingResponse:
    """Stream DP-level consistency and association summary records."""
    _get_run_or_404(run_id)
    query: dict[str, Any] = {"run_id": run_id}
    if consistency:
        normalized = consistency.strip().upper()
        if normalized not in {"CONSISTENT", "CONFLICT", "UNVERIFIED"}:
            raise HTTPException(status_code=422, detail="Invalid DP consistency filter.")
        query["coordinate_consistency"] = normalized
    cursor = dp_records.find(
        query,
        {
            "_id": 0,
            "dp_id": 1,
            "coordinate_consistency": 1,
            "country_boundary_status": 1,
            "dp_assessment_status": 1,
            "dp_association_min_pass_fraction": 1,
            "dp_location_observed": 1,
            "observed_row_count": 1,
            "valid_coordinate_observation_count": 1,
            "coordinate_conflict_observation_count": 1,
            "eligible_customer_row_count": 1,
            "eligible_distance_assessed_row_count": 1,
            "eligible_distance_pass_row_count": 1,
            "eligible_distance_fail_row_count": 1,
            "association_assessed_row_count": 1,
            "association_pass_row_count": 1,
            "association_fail_row_count": 1,
            "port_capacity": 1,
            "assigned_customer_count": 1,
            "available_port_count": 1,
            "port_capacity_status": 1,
            "port_capacity_exceeded_by": 1,
            "issue_codes": 1,
        },
    ).sort("dp_id_normalized", 1).batch_size(1000)
    fields = [
        "dp_id",
        "coordinate_consistency",
        "country_boundary_status",
        "dp_assessment_status",
        "dp_association_min_pass_fraction",
        "port_capacity",
        "assigned_customer_count",
        "available_port_count",
        "port_capacity_status",
        "port_capacity_exceeded_by",
        "dp_location_observed",
        "observed_row_count",
        "valid_coordinate_observation_count",
        "coordinate_conflict_observation_count",
        "eligible_customer_row_count",
        "eligible_distance_pass_row_count",
        "eligible_distance_fail_row_count",
        "association_assessed_row_count",
        "association_pass_row_count",
        "association_fail_row_count",
        "association_fail_percentage",
        "eligible_distance_fail_percentage",
        "association_evidence",
        "association_threshold_configured",
        "issue_codes",
    ]

    def dp_export_rows() -> Iterator[dict[str, Any]]:
        for document in cursor:
            assessed_rows = int(document.get("eligible_distance_assessed_row_count", 0) or 0)
            failed_rows = int(document.get("eligible_distance_fail_row_count", 0) or 0)
            item = dict(document)
            item["eligible_distance_assessed_row_count"] = assessed_rows
            item["eligible_distance_pass_row_count"] = int(document.get("eligible_distance_pass_row_count", 0) or 0)
            item["eligible_distance_fail_percentage"] = round(failed_rows * 100.0 / assessed_rows, 2) if assessed_rows else None
            association_assessed = int(document.get("association_assessed_row_count", 0) or 0)
            association_failed = int(document.get("association_fail_row_count", 0) or 0)
            item["association_assessed_row_count"] = association_assessed
            item["association_pass_row_count"] = int(document.get("association_pass_row_count", 0) or 0)
            item["association_fail_percentage"] = round(association_failed * 100.0 / association_assessed, 2) if association_assessed else None
            item["association_evidence"] = (
                "INSUFFICIENT_ELIGIBLE_ROWS" if association_assessed == 0
                else "ASSOCIATION_FAILURES_OBSERVED" if association_failed > 0
                else "NO_ASSOCIATION_FAILURES_OBSERVED"
            )
            item["association_threshold_configured"] = True
            yield item

    return StreamingResponse(
        _csv_stream(dp_export_rows(), fields),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="geocheck-{run_id}-dps.csv"'},
    )
