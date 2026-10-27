"""Measure GeoCheck's streaming validation pipeline on synthetic CSV data.

Safety: requires GEOCHECK_BENCH_MONGO_URI explicitly and creates a uniquely named
temporary MongoDB database. The database and temporary CSV are deleted after the run
unless --keep-db is supplied. Never point this script at a production database.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import random
import sys
import tempfile
import time
import tracemalloc
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import ASCENDING, MongoClient

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.validation.batch_processor import process_csv_file
from app.validation.csv_import import detect_mapping


def load_generator():
    generator_path = ROOT / "test-data" / "generate_synthetic_csv.py"
    spec = importlib.util.spec_from_file_location("geocheck_synthetic_generator", generator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the synthetic CSV generator.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def generate_fixture(path: Path, *, rows: int, dp_count: int, seed: int, report_every: int) -> tuple[int, float]:
    generator = load_generator()
    rng = random.Random(seed)
    dp_points = [
        (6.90 + rng.uniform(-0.04, 0.04), 79.85 + rng.uniform(-0.04, 0.04))
        for _ in range(dp_count)
    ]
    generated = 0
    start = time.perf_counter()
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=generator.COLUMNS)
        writer.writeheader()
        for index in range(rows):
            writer.writerow(generator.build_row(index, rows, dp_count, dp_points, rng))
            generated += 1
            if report_every and generated % report_every == 0:
                print(f"Synthetic rows generated: {generated:,}/{rows:,}", file=sys.stderr, flush=True)
    return generated, time.perf_counter() - start


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    mongo_uri = args.mongo_uri or __import__("os").environ.get("GEOCHECK_BENCH_MONGO_URI")
    if not mongo_uri:
        raise SystemExit(
            "Set GEOCHECK_BENCH_MONGO_URI to a disposable MongoDB instance. "
            "The script will not fall back to MONGO_URI."
        )

    client = MongoClient(mongo_uri, appname="GeoCheckNextBenchmark", serverSelectionTimeoutMS=5000)
    database_name = "geocheck_bench_" + uuid.uuid4().hex
    db = client[database_name]
    run_id = uuid.uuid4().hex
    try:
        client.admin.command("ping")
        validation_runs = db["validation_runs"]
        customer_records = db["customer_records"]
        dp_records = db["dp_records"]
        validation_runs.create_index([("run_id", ASCENDING)], unique=True, name="bench_run_id_unique")
        customer_records.create_index(
            [("run_id", ASCENDING), ("row_number", ASCENDING)],
            unique=True,
            name="bench_run_source_row_unique",
        )
        dp_records.create_index(
            [("run_id", ASCENDING), ("dp_id_normalized", ASCENDING)],
            unique=True,
            name="bench_run_dp_unique",
        )

        with tempfile.TemporaryDirectory(prefix="geocheck-bench-") as temp:
            csv_path = Path(temp) / "synthetic-customers.csv"
            rows_generated, generation_seconds = generate_fixture(
                csv_path,
                rows=args.rows,
                dp_count=args.dp_count,
                seed=args.seed,
                report_every=args.report_every,
            )
            header = generator_header(csv_path)
            mapping = detect_mapping(header)
            created = datetime.now(timezone.utc)
            validation_runs.insert_one(
                {
                    "run_id": run_id,
                    "status": "QUEUED",
                    "created_at": created,
                    "original_filename": "synthetic-customers.csv",
                    "rows_processed": 0,
                    "source_rows_seen": 0,
                    "issue_counts": {},
                }
            )

            tracemalloc.start()
            start = time.perf_counter()
            result = process_csv_file(
                csv_path,
                run_id=run_id,
                mapping=mapping,
                validation_runs=validation_runs,
                customer_records=customer_records,
                dp_records=dp_records,
                batch_size=args.batch_size,
                max_rows=args.rows,
            )
            validation_seconds = time.perf_counter() - start
            _, peak_python_bytes = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            stored_rows = customer_records.count_documents({"run_id": run_id})
            dp_count_stored = dp_records.count_documents({"run_id": run_id})
            run_document = validation_runs.find_one({"run_id": run_id}, {"_id": 0, "status": 1})
            reconciles = (
                stored_rows == rows_generated
                and int(result.get("rows_processed", -1)) == rows_generated
                and run_document is not None
                and run_document.get("status") == "COMPLETED"
            )
            report = {
                "status": "PASS" if reconciles else "FAIL",
                "synthetic_only": True,
                "run_id": run_id,
                "temporary_database": database_name,
                "configured_input_rows": args.rows,
                "rows_generated": rows_generated,
                "dp_locations_configured": args.dp_count,
                "customer_rows_stored": stored_rows,
                "dp_records_stored": dp_count_stored,
                "batch_size": args.batch_size,
                "batches_processed": result.get("batches_processed"),
                "rows_with_findings": result.get("rows_with_findings"),
                "rows_with_hard_errors": result.get("rows_with_hard_errors"),
                "finding_count": result.get("finding_count"),
                "generation_seconds": round(generation_seconds, 3),
                "validation_seconds": round(validation_seconds, 3),
                "validation_rows_per_second": round(rows_generated / validation_seconds, 2)
                if validation_seconds else None,
                "peak_python_heap_mib_during_validation": round(peak_python_bytes / (1024 * 1024), 2),
                "reconciliation_passed": reconciles,
                "note": (
                    "Performance is environment-specific. Python heap excludes MongoDB server memory, "
                    "database cache, index storage, and some native-library memory. Reference-dependent "
                    "geographic checks are UNVERIFIED unless reference callbacks are configured."
                ),
            }
            print(json.dumps(report, indent=2))
            if args.report:
                report_path = args.report.resolve()
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                print(f"Report saved to: {report_path}", file=sys.stderr)
            if not reconciles:
                raise RuntimeError(
                    f"Benchmark reconciliation failed: generated={rows_generated}, stored={stored_rows}, "
                    f"processed={result.get('rows_processed')}, status={run_document and run_document.get('status')}"
                )
            return report
    finally:
        if not args.keep_db:
            client.drop_database(database_name)
        client.close()


def generator_header(csv_path: Path) -> list[str]:
    with csv_path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream, strict=True)
        return next(reader, [])


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark GeoCheck validation using synthetic CSV data.")
    parser.add_argument("--rows", type=int, default=10_000, help="Synthetic customer rows (default: 10,000).")
    parser.add_argument("--dp-count", type=int, default=2_500, help="Synthetic DP locations (default: 2,500).")
    parser.add_argument("--batch-size", type=int, default=1000, help="Rows validated per batch.")
    parser.add_argument("--seed", type=int, default=20261027, help="Seed for reproducible data.")
    parser.add_argument("--report", type=Path, help="Optional path where the JSON report should be saved.")
    parser.add_argument("--keep-db", action="store_true", help="Keep the uniquely named temporary database for inspection.")
    parser.add_argument("--report-every", type=int, default=100_000, help="Progress message interval; use 0 to silence.")
    parser.add_argument("--mongo-uri", help="Disposable MongoDB URI; otherwise GEOCHECK_BENCH_MONGO_URI is required.")
    args = parser.parse_args()
    if args.rows < 1 or args.dp_count < 1 or args.batch_size < 1:
        parser.error("--rows, --dp-count and --batch-size must all be at least 1.")
    if args.report_every < 0:
        parser.error("--report-every cannot be negative.")
    run_benchmark(args)


if __name__ == "__main__":
    main()
