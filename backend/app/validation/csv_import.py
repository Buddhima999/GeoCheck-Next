"""CSV header mapping and streaming row import helpers.

The module does not use pandas and does not materialize the whole file. Source
row numbers include the header row (the first customer row is row 2).
"""
from __future__ import annotations

import csv
import re
from collections.abc import Iterator, Mapping
from typing import TextIO


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "customer_id": ("customerid", "custid", "subscriberid", "accountno", "accountnumber", "customer_id"),
    "phone_number": ("phonenumber", "phone", "mobile", "contactnumber", "telephone", "phone_number"),
    "router_number": ("routernumber", "router_number", "routerno", "service_number", "servicenumber", "subscriber_number"),
    "address": ("address", "customeraddress", "installationaddress", "fulladdress", "siteaddress", "custaddress", "cust_address"),
    "customer_latitude": ("customerlatitude", "customerlat", "custlat", "latitude", "lat", "customer_latitude"),
    "customer_longitude": ("customerlongitude", "customerlon", "customerlong", "custlon", "longitude", "lon", "lng", "customer_longitude"),
    "dp_id": ("dpid", "fdpid", "distributionpointid", "fdp", "dp", "dp_id"),
    "dp_latitude": ("dplatitude", "dplat", "fdplatitude", "fdplat", "dp_latitude"),
    "dp_longitude": ("dplongitude", "dplon", "dplong", "fdplongitude", "fdplon", "dp_longitude"),
    "line_distance_m": ("linedistancem", "linedistance", "cabledistance", "dropdistance", "line_distance_m"),
    "overhead_distance_m": ("overheaddistancem", "overheaddistance", "aerialdistance", "overhead_distance_m"),
    "reported_province": ("reportedprovince", "province", "reported_province"),
    "reported_district": ("reporteddistrict", "district", "reported_district"),
    "reported_region": ("reportedregion", "region", "operationalregion", "reported_region"),
}

REQUIRED_COORDINATE_FIELDS = (
    "customer_latitude", "customer_longitude", "dp_latitude", "dp_longitude"
)

# Required for the customer/DP fault-audit upload profile. phone_number remains
# accepted as a legacy alternative to router_number.
REQUIRED_AUDIT_FIELDS = (
    "customer_id", "router_number", "customer_latitude", "customer_longitude",
    "address", "dp_id", "dp_latitude", "dp_longitude", "line_distance_m",
)


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def normalize_identifier(value: object) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", str(value)).strip().casefold()
    return normalized or None


def detect_mapping(headers: list[str]) -> dict[str, str]:
    """Detect logical-field -> source-header mapping, preferring exact aliases."""
    result: dict[str, str] = {}
    used: set[str] = set()
    normalized_headers = {h: normalize_header(h) for h in headers}
    for logical, aliases in FIELD_ALIASES.items():
        candidates = {normalize_header(alias) for alias in aliases}
        match = next((h for h in headers if h not in used and normalized_headers[h] in candidates), None)
        if match is not None:
            result[logical] = match
            used.add(match)
    return result


def validate_mapping(
    headers: list[str],
    mapping: Mapping[str, str],
    *,
    required_fields: tuple[str, ...] = REQUIRED_COORDINATE_FIELDS,
) -> None:
    """Raise ValueError when the mapping points to missing/ambiguous columns."""
    if not headers:
        raise ValueError("CSV has no header row")
    if len({normalize_header(h) for h in headers}) != len(headers):
        raise ValueError("CSV contains duplicate column headers after normalization")
    for field in required_fields:
        header = mapping.get(field)
        if not header:
            raise ValueError(f"Required mapping is missing: {field}")
        if header not in headers:
            raise ValueError(f"Mapped source column does not exist: {header}")
    used = [mapping[k] for k in mapping if mapping[k] in headers]
    if len(used) != len(set(used)):
        raise ValueError("One source column cannot map to multiple logical fields")


def validate_audit_mapping(headers: list[str], mapping: Mapping[str, str]) -> None:
    """Validate the nine-column customer/DP audit schema while accepting legacy phone_number."""
    validate_mapping(headers, mapping)
    missing = [
        field for field in REQUIRED_AUDIT_FIELDS
        if not mapping.get(field) and not (field == "router_number" and mapping.get("phone_number"))
    ]
    if missing:
        raise ValueError(f"Required audit mapping is missing: {', '.join(missing)}")
    for field in REQUIRED_AUDIT_FIELDS:
        selected = mapping.get(field)
        if field == "router_number" and not selected:
            selected = mapping.get("phone_number")
        if selected and selected not in headers:
            raise ValueError(f"Mapped source column does not exist: {selected}")


def iter_mapped_rows(
    stream: TextIO,
    mapping: Mapping[str, str],
    *,
    max_rows: int | None = None,
    max_columns: int = 64,
    max_cell_chars: int = 4096,
    include_blank_rows: bool = False,
) -> Iterator[dict[str, object]]:
    """Yield mapped source rows one at a time; never reads all rows into RAM."""
    reader = csv.DictReader(stream, strict=True)
    headers = reader.fieldnames or []
    validate_mapping(headers, mapping)
    if len(headers) > max_columns:
        raise ValueError(f"CSV has {len(headers)} columns; maximum is {max_columns}")

    for source_row_number, raw in enumerate(reader, start=2):
        if max_rows is not None and source_row_number - 1 > max_rows:
            raise ValueError(f"CSV exceeds the maximum of {max_rows} data rows")
        if None in raw:
            raise ValueError(f"CSV row {source_row_number} has more values than headers")
        values = {str(k): (v or "").strip() for k, v in raw.items() if k is not None}
        if any(len(v) > max_cell_chars for v in values.values()):
            raise ValueError(f"CSV row {source_row_number} contains a cell over {max_cell_chars} characters")
        mapped = {logical: values.get(header, "") for logical, header in mapping.items()}
        if not any(values.values()):
            if include_blank_rows:
                yield {
                    "row_number": source_row_number,
                    "raw": values,
                    "mapped": mapped,
                    "is_blank": True,
                }
            continue
        yield {
            "row_number": source_row_number,
            "raw": values,
            "mapped": mapped,
            "is_blank": False,
        }


def preview_csv(stream: TextIO, *, sample_size: int = 5) -> dict[str, object]:
    """Read headers and a small preview. Reopen the file to perform the real streaming import."""
    reader = csv.DictReader(stream, strict=True)
    headers = reader.fieldnames or []
    if not headers:
        raise ValueError("CSV has no header row")
    mapping = detect_mapping(headers)
    sample = []
    count = 0
    for row in reader:
        if None in row:
            raise ValueError(f"CSV row {count + 2} has more values than headers")
        clean = {str(k): (v or "").strip() for k, v in row.items() if k is not None}
        if any(clean.values()):
            count += 1
            if len(sample) < sample_size:
                sample.append(clean)
    return {
        "headers": headers,
        "detected_mapping": mapping,
        "sample_rows": sample,
        "total_rows": count,
    }
