"""Deterministic, reference-data-aware validation rules.

Reference-dependent geography checks are injected. If approved reference data is
unavailable, the result is UNVERIFIED rather than a guessed PASS or FAIL.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

PASS = "PASS"
FAIL = "FAIL"
UNVERIFIED = "UNVERIFIED"
NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class ValidationSettings:
    customer_dp_limit_m: float = 500.0
    # The requested customer/line threshold is 500 m; override only with provider approval.
    max_line_distance_m: float | None = 500.0
    distance_method: str = "straight_line"
    address_match_tolerance_m: float = 500.0
    overhead_distance_tolerance_m: float = 25.0
    # Provider confirmed overhead_distance_m is straight-line customer-to-DP distance.
    overhead_distance_is_straight_line: bool = True
    dp_coordinate_tolerance_m: float = 50.0
    # A rough envelope is used only to diagnose possible coordinate swaps.
    # It is NOT a legal country boundary.
    sri_lanka_lat_range: tuple[float, float] = (5.7, 10.1)
    sri_lanka_lon_range: tuple[float, float] = (79.3, 82.1)


def as_float(value: object) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate straight-line surface distance in metres, not cable-route length."""
    radius_m = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = math.sin(delta_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(delta_lon / 2) ** 2
    return 2 * radius_m * math.asin(math.sqrt(min(1.0, a)))


def _check(status: str, *, distance_m: float | None = None, message: str = "") -> dict[str, object]:
    result: dict[str, object] = {"status": status}
    if distance_m is not None:
        result["distance_m"] = round(distance_m, 1)
    if message:
        result["message"] = message
    return result


def validate_row(
    row: Mapping[str, object],
    *,
    settings: ValidationSettings = ValidationSettings(),
    country_check: Callable[[float, float], bool | None] | None = None,
    phone_area_check: Callable[[str, float, float], bool | None] | None = None,
    address_zone_check: Callable[[str, float, float], bool | None] | None = None,
    geography_check: Callable[[Mapping[str, object], float, float], bool | None] | None = None,
    route_distance_check: Callable[[float, float, float, float], float | None] | None = None,
    geocode_address_check: Callable[[str, float, float], float | None] | None = None,
) -> dict[str, object]:
    """Validate one mapped source row. Duplicate/conflict checks are cross-row rules."""
    row_number = row.get("row_number")
    values = row.get("mapped", row)
    if not isinstance(values, Mapping):
        raise TypeError("mapped row must contain a mapping")

    customer_id = str(values.get("customer_id", "") or "").strip()
    router_number = str(values.get("router_number", values.get("phone_number", "")) or "").strip()
    dp_id = str(values.get("dp_id", "") or "").strip()
    issue_codes: list[str] = []
    checks: dict[str, dict[str, object]] = {}
    findings: list[dict[str, str]] = []

    def add(code: str, status: str, severity: str, message: str, field: str | None = None) -> None:
        if code in issue_codes:
            return
        issue_codes.append(code)
        item = {"code": code, "status": status, "severity": severity, "message": message}
        if field:
            item["field"] = field
        findings.append(item)

    if not customer_id:
        add("CUSTOMER_ID_MISSING", FAIL, "error", "Customer ID is missing.", "customer_id")
    if not router_number:
        add("ROUTER_NUMBER_MISSING", FAIL, "error", "Router/service number is missing.", "router_number")
    if not dp_id:
        add("DP_ID_MISSING", UNVERIFIED, "warning", "DP ID is missing; association cannot be checked.", "dp_id")

    def coordinate_pair(prefix: str, lat_field: str, lon_field: str) -> tuple[float | None, float | None, str]:
        raw_lat, raw_lon = values.get(lat_field), values.get(lon_field)
        lat, lon = as_float(raw_lat), as_float(raw_lon)
        code_prefix = "DP_COORD" if prefix == "dp_coordinates" else "COORD"

        if str(raw_lat or "").strip() == "" or str(raw_lon or "").strip() == "":
            checks[prefix] = _check(FAIL, message="One or both coordinates are missing.")
            add(f"{code_prefix}_MISSING", FAIL, "error", f"{prefix.replace('_', ' ').title()} coordinates are missing.", lat_field if str(raw_lat or "").strip() == "" else lon_field)
            return None, None, "invalid"
        if lat is None or lon is None:
            checks[prefix] = _check(FAIL, message="A coordinate is not a finite number.")
            add(f"{code_prefix}_NOT_NUMERIC", FAIL, "error", f"{prefix.replace('_', ' ').title()} coordinates must be finite numbers.", lat_field if lat is None else lon_field)
            return None, None, "invalid"
        if abs(lat) > 90 or abs(lon) > 180:
            checks[prefix] = _check(FAIL, message="Coordinate is outside the global latitude/longitude range.")
            add(f"{code_prefix}_OUT_OF_RANGE", FAIL, "error", f"{prefix.replace('_', ' ').title()} coordinate is outside the global range.", lat_field if abs(lat) > 90 else lon_field)
            return None, None, "invalid"
        if lat == 0 and lon == 0:
            checks[prefix] = _check(FAIL, message="(0, 0) is treated as an unknown-location placeholder.")
            add(f"{code_prefix}_ZERO_PLACEHOLDER", FAIL, "error", f"{prefix.replace('_', ' ').title()} coordinates use the (0, 0) placeholder.", lat_field)
            return None, None, "invalid"

        min_lat, max_lat = settings.sri_lanka_lat_range
        min_lon, max_lon = settings.sri_lanka_lon_range
        original_in_envelope = min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
        swapped_in_envelope = min_lat <= lon <= max_lat and min_lon <= lat <= max_lon
        if not original_in_envelope and swapped_in_envelope:
            checks[prefix] = _check(UNVERIFIED, message="Coordinates may be swapped; dependent checks are suppressed.")
            add(f"{code_prefix}_POSSIBLE_SWAP", UNVERIFIED, "warning", f"{prefix.replace('_', ' ').title()} coordinates may have latitude and longitude swapped.", lat_field)
            return lat, lon, "possible_swap"

        checks[prefix] = _check(PASS)
        return lat, lon, "valid"

    c_lat, c_lon, c_state = coordinate_pair("customer_coordinates", "customer_latitude", "customer_longitude")
    d_lat, d_lon, d_state = coordinate_pair("dp_coordinates", "dp_latitude", "dp_longitude")

    if c_state != "valid":
        status = UNVERIFIED if c_state == "possible_swap" else NOT_APPLICABLE
        message = "Possible coordinate swap; dependent checks suppressed." if c_state == "possible_swap" else "Coordinates are not usable."
        for name in ("country_boundary", "address_zone", "phone_area", "reported_geography"):
            checks[name] = _check(status, message=message)
    else:
        assert c_lat is not None and c_lon is not None
        if country_check is None:
            checks["country_boundary"] = _check(UNVERIFIED, message="Approved land-boundary data is not configured.")
        else:
            outcome = country_check(c_lat, c_lon)
            checks["country_boundary"] = _check(UNVERIFIED if outcome is None else PASS if outcome else FAIL)
            if outcome is False:
                add("COUNTRY_BOUNDARY", FAIL, "error", "Customer point is outside the accepted geographic boundary.", "customer_latitude")

        address = str(values.get("address", "") or "").strip()
        if not address:
            checks["address_zone"] = _check(FAIL, message="Customer address is missing.")
            add("ADDRESS_MISSING", FAIL, "error", "Customer address is missing.", "address")
        elif geocode_address_check is not None:
            # Google Geocoding provides a resolved point. A failed lookup stays unverified;
            # it must not be mistaken for a confirmed mismatch or a pass.
            address_distance = geocode_address_check(address, c_lat, c_lon)
            if address_distance is None:
                checks["address_zone"] = _check(UNVERIFIED, message="Google could not verify this address within the configured request limit.")
                add("ADDRESS_UNVERIFIED", UNVERIFIED, "info", "Address could not be matched to a resolved Google Maps location.", "address")
            else:
                address_matches = address_distance <= settings.address_match_tolerance_m
                checks["address_zone"] = _check(
                    PASS if address_matches else FAIL,
                    distance_m=address_distance,
                    message=f"Customer coordinate is {address_distance:.1f} m from the Google-geocoded address point; tolerance is {settings.address_match_tolerance_m:.1f} m.",
                )
                if not address_matches:
                    add("ADDRESS_LOCATION_MISMATCH", FAIL, "warning", "Customer coordinates are too far from the Google-geocoded address point.", "address")
        elif address_zone_check is None:
            checks["address_zone"] = _check(UNVERIFIED, message="Google Geocoding is disabled and approved locality zones are not configured.")
            add("ADDRESS_UNVERIFIED", UNVERIFIED, "info", "Address location cannot be verified without Google Geocoding or approved locality zones.", "address")
        else:
            outcome = address_zone_check(address, c_lat, c_lon)
            checks["address_zone"] = _check(UNVERIFIED if outcome is None else PASS if outcome else FAIL)
            if outcome is None:
                add("ADDRESS_UNVERIFIED", UNVERIFIED, "info", "Address locality could not be resolved using the configured reference data.", "address")
            elif outcome is False:
                add("ADDRESS_ZONE_MISMATCH", FAIL, "warning", "Coordinates conflict with the resolved address zone.", "address")

        phone = re.sub(r"[^0-9+]", "", str(values.get("phone_number", "") or ""))
        if not phone:
            checks["phone_area"] = _check(NOT_APPLICABLE, message="Router numbers are not assumed to be geographic telephone numbers.")
        elif phone.startswith("07") or phone.startswith("+947"):
            checks["phone_area"] = _check(UNVERIFIED, message="Mobile numbers do not establish a town-level location.")
        elif phone_area_check is None:
            checks["phone_area"] = _check(UNVERIFIED, message="Approved LEA/area-code reference is not configured.")
        else:
            outcome = phone_area_check(phone, c_lat, c_lon)
            checks["phone_area"] = _check(UNVERIFIED if outcome is None else PASS if outcome else FAIL)
            if outcome is False:
                add("PHONE_AREA_MISMATCH", FAIL, "warning", "Phone area reference conflicts with customer location.", "phone_number")

        reported_geography_fields = ("reported_province", "reported_district", "reported_region")
        has_reported_geography = any(str(values.get(field, "") or "").strip() for field in reported_geography_fields)
        if not has_reported_geography:
            checks["reported_geography"] = _check(NOT_APPLICABLE, message="No reported province, district or region was supplied.")
        elif geography_check is None:
            checks["reported_geography"] = _check(UNVERIFIED, message="Authoritative geography reference is not configured.")
        else:
            outcome = geography_check(values, c_lat, c_lon)
            checks["reported_geography"] = _check(UNVERIFIED if outcome is None else PASS if outcome else FAIL)
            if outcome is False:
                add("REPORTED_GEOGRAPHY_MISMATCH", FAIL, "warning", "Reported geography conflicts with resolved assignment.")

    direct_distance: float | None = None
    if c_state == "valid" and d_state == "valid":
        assert c_lat is not None and c_lon is not None and d_lat is not None and d_lon is not None
        direct_distance = haversine_m(c_lat, c_lon, d_lat, d_lon)
        direct_okay = direct_distance <= settings.customer_dp_limit_m
        checks["customer_dp_direct_distance"] = _check(
            PASS if direct_okay else FAIL,
            distance_m=direct_distance,
            message="Direct geographic distance calculated from customer and DP coordinates; not cable-route length.",
        )
        if settings.distance_method == "google_routes":
            route_distance = route_distance_check(c_lat, c_lon, d_lat, d_lon) if route_distance_check else None
            if route_distance is None:
                checks["google_route_distance"] = _check(
                    UNVERIFIED,
                    message="Google Maps route distance is unavailable (API key, route response or per-run request cap may be responsible).",
                )
                checks["customer_dp_distance"] = _check(
                    UNVERIFIED,
                    distance_m=direct_distance,
                    message="Google Routes mode is enabled, but no route distance was returned. The direct distance is shown separately and is not substituted for the route result.",
                )
                add("CUSTOMER_DP_ROUTE_UNVERIFIED", UNVERIFIED, "info", "The Google Maps route could not be verified; the 500 m route check was not passed or failed.")
            else:
                route_okay = route_distance <= settings.customer_dp_limit_m
                checks["google_route_distance"] = _check(
                    PASS if route_okay else FAIL,
                    distance_m=route_distance,
                    message="Google Maps driving-road distance; this is not actual overhead cable length.",
                )
                checks["customer_dp_distance"] = _check(
                    PASS if route_okay else FAIL,
                    distance_m=route_distance,
                    message=f"Google Maps driving-route distance compared with the {settings.customer_dp_limit_m:.1f} m limit.",
                )
                checks["customer_dp_distance"]["method"] = "google_routes"
                if not route_okay:
                    add("CUSTOMER_DP_DISTANCE", FAIL, "warning", f"Google Maps route from customer to DP is {route_distance:.1f} m; configured limit is {settings.customer_dp_limit_m:.1f} m.")
        else:
            checks["customer_dp_distance"] = _check(
                PASS if direct_okay else FAIL,
                distance_m=direct_distance,
                message=f"Direct coordinate distance compared with the {settings.customer_dp_limit_m:.1f} m limit.",
            )
            checks["customer_dp_distance"]["method"] = "straight_line"
            if not direct_okay:
                add("CUSTOMER_DP_DISTANCE", FAIL, "warning", f"Customer is {direct_distance:.1f} m from DP; configured limit is {settings.customer_dp_limit_m:.1f} m.")
    else:
        checks["customer_dp_direct_distance"] = _check(NOT_APPLICABLE, message="Customer or DP coordinates are unusable.")
        checks["customer_dp_distance"] = _check(NOT_APPLICABLE, message="Customer or DP coordinates are unusable.")
        if settings.distance_method == "google_routes":
            checks["google_route_distance"] = _check(NOT_APPLICABLE, message="Coordinates are unusable.")

    checks["customer_inside_sri_lanka"] = dict(checks.get("country_boundary", _check(UNVERIFIED)))

    line_raw = str(values.get("line_distance_m", "") or "").strip()
    line_distance = as_float(line_raw)
    if not line_raw:
        checks["line_distance"] = _check(FAIL, message="Reported line distance is missing.")
        add("LINE_DISTANCE_MISSING", FAIL, "error", "Reported line distance is missing.", "line_distance_m")
    elif line_distance is None or line_distance < 0:
        checks["line_distance"] = _check(FAIL, message="Line distance must be a non-negative finite number.")
        add("LINE_DISTANCE_INVALID", FAIL, "error", "Line distance must be a non-negative finite number.", "line_distance_m")
    elif settings.max_line_distance_m is not None and line_distance > settings.max_line_distance_m:
        checks["line_distance"] = _check(FAIL, distance_m=line_distance, message="Reported line distance exceeds the configured 500 m limit.")
        add("LINE_DISTANCE_LIMIT", FAIL, "warning", f"Line distance exceeds {settings.max_line_distance_m:.1f} m.", "line_distance_m")
    else:
        checks["line_distance"] = _check(PASS, distance_m=line_distance, message="Reported line distance is finite, non-negative and within the configured limit.")

    overhead_raw = str(values.get("overhead_distance_m", "") or "").strip()
    overhead_distance = as_float(overhead_raw)
    if not overhead_raw:
        checks["overhead_distance"] = _check(NOT_APPLICABLE, message="No overhead distance supplied.")
    elif overhead_distance is None or overhead_distance < 0:
        checks["overhead_distance"] = _check(FAIL, message="Overhead distance must be a non-negative finite number.")
        add("OVERHEAD_DISTANCE_INVALID", FAIL, "error", "Overhead distance must be a non-negative finite number.", "overhead_distance_m")
    elif not settings.overhead_distance_is_straight_line:
        checks["overhead_distance"] = _check(
            UNVERIFIED,
            distance_m=overhead_distance,
            message="Value is non-negative and finite, but its relationship to straight-line distance has not been confirmed.",
        )
    elif c_state == "valid" and d_state == "valid":
        assert c_lat is not None and c_lon is not None and d_lat is not None and d_lon is not None
        calculated = haversine_m(c_lat, c_lon, d_lat, d_lon)
        delta = abs(overhead_distance - calculated)
        okay = delta <= settings.overhead_distance_tolerance_m
        checks["overhead_distance"] = _check(PASS if okay else FAIL, distance_m=delta, message=f"Difference from coordinate-derived distance is {delta:.1f} m.")
        if not okay:
            add("OVERHEAD_DISTANCE_MISMATCH", FAIL, "warning", f"Overhead distance differs by {delta:.1f} m from coordinate-derived distance.", "overhead_distance_m")
    else:
        checks["overhead_distance"] = _check(UNVERIFIED, message="Cannot compare without valid coordinates.")

    overall_status = (
        FAIL if any(item.get("status") == FAIL for item in findings)
        else UNVERIFIED if any(
            checks.get(name, {}).get("status") == UNVERIFIED
            for name in ("country_boundary", "address_zone", "customer_dp_distance", "line_distance", "overhead_distance")
        ) or any(item.get("status") == UNVERIFIED for item in findings)
        else PASS
    )
    raw_values = row.get("raw")
    source_row_values = dict(raw_values) if isinstance(raw_values, Mapping) else dict(values)

    return {
        "row_number": row_number,
        "customer_id": customer_id or None,
        "router_number": router_number or None,
        "calculated_customer_dp_distance_m": round(direct_distance, 1) if direct_distance is not None else None,
        "distance_method": settings.distance_method,
        "customer_inside_sri_lanka": checks.get("country_boundary", {}).get("status", UNVERIFIED),
        "overall_status": overall_status,
        "has_fault": overall_status == FAIL,
        "source_row_values": source_row_values,
        "customer_id_normalized": re.sub(r"\s+", " ", customer_id).casefold() or None,
        "dp_id": dp_id or None,
        "dp_id_normalized": re.sub(r"\s+", " ", dp_id).casefold() or None,
        "customer_location": {"lat": c_lat, "lon": c_lon} if c_lat is not None and c_lon is not None else None,
        "dp_location_observed": {"lat": d_lat, "lon": d_lon} if d_lat is not None and d_lon is not None else None,
        "source_values": {str(k): v for k, v in values.items()},
        "checks": checks,
        "issue_codes": issue_codes,
        "findings": findings,
        "has_hard_error": any(f["severity"] == "error" for f in findings),
    }


def validate_dataset_small(
    mapped_rows: Iterable[Mapping[str, object]],
    *,
    settings: ValidationSettings = ValidationSettings(),
    **reference_checks: object,
) -> list[dict[str, object]]:
    """In-memory cross-row helper for small fixtures only, not the 600k import."""
    rows = list(mapped_rows)
    results = [validate_row(row, settings=settings, **reference_checks) for row in rows]  # type: ignore[arg-type]
    customer_groups: dict[str, list[int]] = defaultdict(list)
    dp_groups: dict[str, list[int]] = defaultdict(list)
    for index, result in enumerate(results):
        if result["customer_id_normalized"]:
            customer_groups[str(result["customer_id_normalized"])].append(index)
        if result["dp_id_normalized"] and result["dp_location_observed"]:
            dp_groups[str(result["dp_id_normalized"])].append(index)

    for indexes in customer_groups.values():
        if len(indexes) < 2:
            continue
        signatures = set()
        for index in indexes:
            location = results[index]["customer_location"]
            source = results[index]["source_values"]
            assert isinstance(source, Mapping)
            address = re.sub(r"\s+", " ", str(source.get("address", "") or "")).strip().casefold()
            phone = re.sub(r"\D", "", str(source.get("phone_number", "") or ""))
            signatures.add((
                None if location is None else round(float(location["lat"]), 5),
                None if location is None else round(float(location["lon"]), 5),
                address, phone,
            ))
        if len(signatures) > 1:
            for index in indexes:
                _append_cross_finding(results[index], "CUSTOMER_ID_CONFLICT", "Customer ID occurs on multiple rows with different coordinates or identifying details.")

    for indexes in dp_groups.values():
        if len(indexes) < 2:
            continue
        points = []
        for index in indexes:
            location = results[index]["dp_location_observed"]
            assert isinstance(location, Mapping)
            points.append((index, float(location["lat"]), float(location["lon"])))
        conflicts: set[int] = set()
        for i, a in enumerate(points):
            for b in points[i + 1:]:
                if haversine_m(a[1], a[2], b[1], b[2]) > settings.dp_coordinate_tolerance_m:
                    conflicts.update((a[0], b[0]))
        for index in conflicts:
            _append_cross_finding(results[index], "DP_COORD_CONFLICT", "Same DP ID has coordinate observations farther apart than configured tolerance.")
    return results


def _append_cross_finding(result: dict[str, object], code: str, message: str) -> None:
    codes = result["issue_codes"]
    findings = result["findings"]
    assert isinstance(codes, list) and isinstance(findings, list)
    if code not in codes:
        codes.append(code)
        findings.append({"code": code, "status": FAIL, "severity": "warning", "message": message})
