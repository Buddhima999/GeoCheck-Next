"""Live MongoDB tests; run only against a disposable database/container.

Set GEOCHECK_TEST_MONGO_URI to enable these tests. They create and drop a uniquely
named temporary database and use synthetic rows only.
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pymongo import ASCENDING, MongoClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import worker
from app.config import settings
from app.validation.csv_import import detect_mapping

MONGO_URI = os.getenv("GEOCHECK_TEST_MONGO_URI")
HEADERS = [
    "customer_id", "phone_number", "address",
    "customer_latitude", "customer_longitude",
    "dp_id", "dp_latitude", "dp_longitude",
    "line_distance_m", "overhead_distance_m",
    "reported_province", "reported_district", "reported_region",
]


@unittest.skipUnless(MONGO_URI, "Set GEOCHECK_TEST_MONGO_URI to run live MongoDB integration tests.")
class MongoIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        cls.client.admin.command("ping")
        cls.db_name = "geocheck_integration_" + uuid.uuid4().hex
        cls.db = cls.client[cls.db_name]
        cls.runs = cls.db["validation_runs"]
        cls.customers = cls.db["customer_records"]
        cls.dps = cls.db["dp_records"]
        cls.runs.create_index([("run_id", ASCENDING)], unique=True)
        cls.runs.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
        cls.customers.create_index([("run_id", ASCENDING), ("row_number", ASCENDING)], unique=True)
        cls.dps.create_index([("run_id", ASCENDING), ("dp_id_normalized", ASCENDING)], unique=True)

    @classmethod
    def tearDownClass(cls):
        cls.client.drop_database(cls.db_name)
        cls.client.close()

    def setUp(self):
        self.runs.delete_many({})
        self.customers.delete_many({})
        self.dps.delete_many({})

    def make_row(self, customer_id, customer_lat, customer_lon, overhead):
        return {
            "customer_id": customer_id,
            "phone_number": "0111234567",
            "address": "Synthetic Sample Town Road",
            "customer_latitude": str(customer_lat),
            "customer_longitude": str(customer_lon),
            "dp_id": "SYN-DP-1",
            "dp_latitude": "6.9000",
            "dp_longitude": "79.8500",
            "line_distance_m": "120",
            "overhead_distance_m": str(overhead),
            "reported_province": "Synthetic Province",
            "reported_district": "Synthetic District",
            "reported_region": "Synthetic Region",
        }

    def test_mongodb_claims_two_jobs_once_under_concurrent_workers(self):
        now = datetime.now(timezone.utc)
        self.runs.insert_many([
            {"run_id": "claim-a", "status": "QUEUED", "created_at": now},
            {"run_id": "claim-b", "status": "QUEUED", "created_at": now},
        ])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(worker.claim_next_run, self.runs, "worker-a"),
                pool.submit(worker.claim_next_run, self.runs, "worker-b"),
            ]
            claimed = [future.result() for future in futures]
        self.assertEqual({item["run_id"] for item in claimed if item}, {"claim-a", "claim-b"})
        self.assertEqual(self.runs.count_documents({"status": "PROCESSING"}), 2)

    def test_real_worker_replay_clears_partial_records_and_reconciles_counts(self):
        rows = [
            self.make_row("SYN-C-1", 6.9001, 79.8501, 16),
            self.make_row("SYN-C-2", 6.9002, 79.8502, 31),
        ]
        run_id = "replay-" + uuid.uuid4().hex
        file_key = uuid.uuid4().hex + ".csv"
        with tempfile.TemporaryDirectory() as temp:
            upload_dir = Path(temp)
            with patch.object(settings, "upload_dir", upload_dir):
                upload_path = worker.resolve_upload_path(file_key)
                with upload_path.open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=HEADERS)
                    writer.writeheader()
                    writer.writerows(rows)

                self.runs.insert_one({
                    "run_id": run_id,
                    "status": "QUEUED",
                    "created_at": datetime.now(timezone.utc),
                    "input_file_key": file_key,
                    "column_mapping": detect_mapping(HEADERS),
                    "attempt_count": 1,
                    "rows_processed": 1,
                    "source_rows_seen": 1,
                    "issue_counts": {"STALE_PARTIAL_ISSUE": 99},
                    "summary": {"rows_processed": 1},
                })
                # Simulate a prior interrupted attempt where the first row and DP rollup
                # were already saved. The retry must remove and rebuild these records.
                self.customers.insert_one({
                    "run_id": run_id,
                    "row_number": 2,
                    "customer_id": "STALE-PARTIAL-CUSTOMER",
                    "issue_codes": ["STALE_PARTIAL_ISSUE"],
                })
                self.dps.insert_one({
                    "run_id": run_id,
                    "dp_id": "STALE-PARTIAL-DP",
                    "dp_id_normalized": "stale-partial-dp",
                    "observed_row_count": 99,
                })

                with patch.object(settings, "max_csv_rows", 100):
                    worked = worker.process_next_queued_run(
                        run_collection=self.runs,
                        customer_collection=self.customers,
                        dp_collection=self.dps,
                        worker_id="integration-worker",
                        batch_size=1,
                        max_rows=100,
                    )

                self.assertTrue(worked)
                run = self.runs.find_one({"run_id": run_id})
                self.assertEqual(run["status"], "COMPLETED", run.get("last_error"))
                self.assertEqual(run["rows_processed"], 2)
                self.assertEqual(run["source_rows_seen"], 2)
                self.assertNotIn("STALE_PARTIAL_ISSUE", run["issue_counts"])
                self.assertEqual(self.customers.count_documents({"run_id": run_id}), 2)
                self.assertEqual(self.customers.count_documents({
                    "run_id": run_id,
                    "customer_id": {"$in": ["SYN-C-1", "SYN-C-2"]},
                }), 2)
                self.assertEqual(self.dps.count_documents({"run_id": run_id}), 1)
                dp = self.dps.find_one({"run_id": run_id})
                self.assertEqual(dp["dp_id"], "SYN-DP-1")
                self.assertEqual(dp["observed_row_count"], 2)
                self.assertNotIn("input_file_key", run)
                self.assertFalse(upload_path.exists())


if __name__ == "__main__":
    unittest.main()
