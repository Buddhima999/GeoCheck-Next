"""Generate synthetic GeoCheck CSV fixtures; never use real customer data here.

Examples (run from the repository root):
  python test-data/generate_synthetic_csv.py
  python test-data/generate_synthetic_csv.py --rows 600000 --dp-count 150000

Coordinates are synthetic points in a small Sri Lanka-like bounding box. They are
for parser/rule/performance tests only, not evidence for country-boundary checks.
"""
from __future__ import annotations

import argparse
import csv
import math
import random
from pathlib import Path

COLUMNS = [
    "customer_id",
    "phone_number",
    "address",
    "customer_latitude",
    "customer_longitude",
    "dp_id",
    "dp_latitude",
    "dp_longitude",
    "line_distance_m",
    "overhead_distance_m",
    "reported_province",
    "reported_district",
    "reported_region",
]

# These are the injected, primary expected findings for the first fixture rows.
SCENARIOS = {
    0: ("COORD_MISSING", "Missing customer latitude"),
    1: ("COORD_NOT_NUMERIC", "Non-numeric customer longitude"),
    2: ("COORD_OUT_OF_RANGE", "Latitude outside global range"),
    3: ("COORD_POSSIBLE_SWAP", "Customer latitude/longitude may be swapped"),
    4: ("DP_COORD_MISSING", "Missing DP latitude"),
    5: ("CUSTOMER_DP_DISTANCE", "Customer is deliberately far from its DP"),
    6: ("LINE_DISTANCE_INVALID", "Negative line distance"),
    7: ("OVERHEAD_DISTANCE_MISMATCH", "Overhead distance differs from coordinates"),
    8: ("CUSTOMER_ID_MISSING", "Missing customer ID"),
    9: ("DP_ID_MISSING", "Missing DP ID"),
    10: ("CUSTOMER_ID_CONFLICT", "Conflicting duplicate customer ID"),
    11: ("CUSTOMER_ID_CONFLICT", "Conflicting duplicate customer ID"),
    12: ("DP_COORD_CONFLICT", "First observation of intentionally conflicting DP"),
    13: ("DP_COORD_CONFLICT", "Second observation of intentionally conflicting DP"),
}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = math.sin(delta_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(delta_lon / 2) ** 2
    return 2 * radius_m * math.asin(math.sqrt(min(1.0, a)))


def build_row(index: int, row_count: int, dp_count: int, dp_points: list[tuple[float, float]], rng: random.Random) -> dict[str, object]:
    dp_index = min((index * dp_count) // max(row_count, 1), dp_count - 1)
    dp_lat, dp_lon = dp_points[dp_index]
    customer_lat = dp_lat + rng.uniform(-0.0002, 0.0002)
    customer_lon = dp_lon + rng.uniform(-0.0002, 0.0002)
    overhead = haversine_m(customer_lat, customer_lon, dp_lat, dp_lon)

    row: dict[str, object] = {
        "customer_id": f"SYN-C-{index + 1:07d}",
        "phone_number": f"011{index + 1:07d}",
        "address": f"Synthetic locality {index % 1000 + 1}, Colombo, test record {index + 1}",
        "customer_latitude": f"{customer_lat:.7f}",
        "customer_longitude": f"{customer_lon:.7f}",
        "dp_id": f"SYN-DP-{dp_index + 1:06d}",
        "dp_latitude": f"{dp_lat:.7f}",
        "dp_longitude": f"{dp_lon:.7f}",
        "line_distance_m": f"{overhead + rng.uniform(10, 60):.1f}",
        "overhead_distance_m": f"{overhead:.1f}",
        "reported_province": "Western",
        "reported_district": "Colombo",
        "reported_region": "SYNTHETIC-REGION-01",
    }

    if index == 0:
        row["customer_latitude"] = ""
    elif index == 1:
        row["customer_longitude"] = "NOT_A_NUMBER"
    elif index == 2:
        row["customer_latitude"] = "120"
    elif index == 3:
        row["customer_latitude"], row["customer_longitude"] = row["customer_longitude"], row["customer_latitude"]
    elif index == 4:
        row["dp_latitude"] = ""
    elif index == 5:
        row["customer_longitude"] = f"{float(row['customer_longitude']) + 0.02:.7f}"
    elif index == 6:
        row["line_distance_m"] = "-10"
    elif index == 7:
        row["overhead_distance_m"] = "999"
    elif index == 8:
        row["customer_id"] = ""
    elif index == 9:
        row["dp_id"] = ""
    elif index in (10, 11):
        row["customer_id"] = "SYN-C-CONFLICT"
        if index == 11:
            row["customer_latitude"] = f"{float(row['customer_latitude']) + 0.005:.7f}"
            row["customer_longitude"] = f"{float(row['customer_longitude']) + 0.005:.7f}"

    # Intentionally create two observations for one DP far apart.
    if index == 12:
        row["dp_id"] = "SYN-DP-CONFLICT"
        row["_conflict_dp_lat"] = dp_lat
        row["_conflict_dp_lon"] = dp_lon
    elif index == 13:
        conflict_lat, conflict_lon = dp_points[min((12 * dp_count) // max(row_count, 1), dp_count - 1)]
        row["dp_id"] = "SYN-DP-CONFLICT"
        row["dp_latitude"] = f"{conflict_lat + 0.02:.7f}"
        row["dp_longitude"] = f"{conflict_lon + 0.02:.7f}"
        row["customer_latitude"] = f"{conflict_lat + 0.02 + rng.uniform(-0.0001, 0.0001):.7f}"
        row["customer_longitude"] = f"{conflict_lon + 0.02 + rng.uniform(-0.0001, 0.0001):.7f}"
        row["overhead_distance_m"] = f"{haversine_m(float(row['customer_latitude']), float(row['customer_longitude']), float(row['dp_latitude']), float(row['dp_longitude'])):.1f}"

    # Keep only the declared CSV columns.
    return {column: row.get(column, "") for column in COLUMNS}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic GeoCheck CSV fixtures.")
    parser.add_argument("--rows", type=int, default=50, help="Number of customer rows (default: 50).")
    parser.add_argument("--dp-count", type=int, default=15, help="Number of synthetic DP locations (default: 15).")
    parser.add_argument("--seed", type=int, default=20261027, help="Random seed for reproducible output.")
    parser.add_argument("--csv-output", type=Path, default=Path("test-data/generated/customers_synthetic.csv"))
    parser.add_argument("--expected-output", type=Path, default=Path("test-data/generated/expected_findings_synthetic.csv"))
    args = parser.parse_args()

    if args.rows < 1:
        parser.error("--rows must be at least 1")
    if args.dp_count < 1:
        parser.error("--dp-count must be at least 1")

    rng = random.Random(args.seed)
    dp_points = [
        (6.90 + rng.uniform(-0.04, 0.04), 79.85 + rng.uniform(-0.04, 0.04))
        for _ in range(args.dp_count)
    ]

    args.csv_output.parent.mkdir(parents=True, exist_ok=True)
    args.expected_output.parent.mkdir(parents=True, exist_ok=True)

    with args.csv_output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for index in range(args.rows):
            writer.writerow(build_row(index, args.rows, args.dp_count, dp_points, rng))

    with args.expected_output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_csv_row", "expected_issue_code", "scenario"])
        writer.writeheader()
        for index, (code, description) in SCENARIOS.items():
            if index < args.rows:
                writer.writerow({
                    "source_csv_row": index + 2,
                    "expected_issue_code": code,
                    "scenario": description,
                })

    print(f"Generated {args.rows:,} synthetic customer rows across {args.dp_count:,} configured DP locations.")
    print(f"CSV: {args.csv_output}")
    print(f"Expected-finding scenarios: {args.expected_output}")
    print("Synthetic only. The expected findings list primary injected scenarios, not every possible cascading result.")


if __name__ == "__main__":
    main()
