"""Tests for worker queue claiming, stale-run handling, and preflight."""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import worker
from app.config import settings
from app.upload_storage import resolve_upload_path
from app.validation.csv_import import detect_mapping


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = [dict(doc) for doc in (documents or [])]

    @classmethod
    def matches(cls, document, query):
        for key, wanted in query.items():
            if key == "$or":
                if not any(cls.matches(document, part) for part in wanted):
                    return False
                continue
            actual = document.get(key)
            if isinstance(wanted, dict):
                for op, value in wanted.items():
                    if op == "$lt" and not (actual is not None and actual < value):
                        return False
                    if op == "$lte" and not (actual is not None and actual <= value):
                        return False
                    if op == "$exists" and ((key in document) != bool(value)):
                        return False
            elif actual != wanted:
                return False
        return True

    @staticmethod
    def apply(document, update):
        for key, value in update.get("$set", {}).items():
            document[key] = value
        for key, value in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + value
        for key in update.get("$unset", {}):
            document.pop(key, None)

    def find(self, query, projection=None):
        return [dict(doc) for doc in self.documents if self.matches(doc, query)]

    def find_one(self, query, projection=None):
        for doc in self.documents:
            if self.matches(doc, query):
                return dict(doc)
        return None

    def find_one_and_update(self, query, update, sort=None, return_document=None):
        candidates = [doc for doc in self.documents if self.matches(doc, query)]
        if not candidates:
            return None
        if sort:
            field, direction = sort[0]
            candidates.sort(key=lambda doc: doc.get(field) or datetime.min.replace(tzinfo=timezone.utc), reverse=direction < 0)
        selected = next(doc for doc in self.documents if doc is candidates[0])
        self.apply(selected, update)
        return dict(selected)

    def update_one(self, query, update):
        for doc in self.documents:
            if self.matches(doc, query):
                self.apply(doc, update)
                return type("UpdateResult", (), {"matched_count": 1, "modified_count": 1})()
        return type("UpdateResult", (), {"matched_count": 0, "modified_count": 0})()

    def delete_many(self, query):
        before = len(self.documents)
        self.documents = [doc for doc in self.documents if not self.matches(doc, query)]
        return type("DeleteResult", (), {"deleted_count": before - len(self.documents)})()

    def insert_many(self, documents, ordered=True):
        self.documents.extend(dict(doc) for doc in documents)
        return type("InsertResult", (), {"inserted_count": len(documents)})()

    def bulk_write(self, operations, ordered=False):
        return None


class WorkerTests(unittest.TestCase):
    def test_queue_claim_is_atomic_in_status_and_increments_attempt(self):
        now = datetime.now(timezone.utc)
        runs = FakeCollection([
            {"run_id": "new", "status": "QUEUED", "created_at": now},
            {"run_id": "old", "status": "QUEUED", "created_at": now - timedelta(minutes=1)},
        ])
        claimed = worker.claim_next_run(runs, "test-worker")
        self.assertEqual(claimed["run_id"], "old")
        self.assertEqual(claimed["status"], "PROCESSING")
        self.assertEqual(claimed["attempt_count"], 1)
        self.assertEqual(runs.find_one({"run_id": "old"})["worker_id"], "test-worker")

    def test_stale_processing_run_is_failed_and_upload_is_removed(self):
        now = datetime.now(timezone.utc)
        old_progress = now - timedelta(hours=1)
        file_key = "a" * 32 + ".csv"
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(settings, "upload_dir", Path(temp)):
                upload_path = resolve_upload_path(file_key)
                upload_path.write_text("synthetic test fixture", encoding="utf-8")
                runs = FakeCollection([
                    {
                        "run_id": "stale-run",
                        "status": "PROCESSING",
                        "created_at": old_progress,
                        "processing_started_at": old_progress,
                        "last_progress_at": old_progress,
                        "input_file_key": file_key,
                    }
                ])
                count = worker.mark_stale_processing_runs(
                    runs,
                    stale_after_seconds=60,
                    max_attempts=1,
                )
                self.assertEqual(count, 1)
                updated = runs.find_one({"run_id": "stale-run"})
                self.assertEqual(updated["status"], "FAILED")
                self.assertEqual(updated["recovery_action"], "REUPLOAD_REQUIRED")
                self.assertNotIn("input_file_key", updated)
                self.assertFalse(upload_path.exists())

    def test_preflight_rejects_malformed_row_before_processing(self):
        headers = [
            "customer_id", "customer_latitude", "customer_longitude",
            "dp_latitude", "dp_longitude",
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "malformed.csv"
            path.write_text(
                ",".join(headers) + "\nC-1,6.9,79.8,6.91,79.81,extra\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "row 2 has more values than headers"):
                worker.preflight_csv_file(path, detect_mapping(headers), max_rows=10)

    def test_preflight_enforces_row_limit_before_processing(self):
        headers = [
            "customer_id", "customer_latitude", "customer_longitude",
            "dp_latitude", "dp_longitude",
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "two-rows.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(headers)
                writer.writerow(["C-1", "6.9", "79.8", "6.91", "79.81"])
                writer.writerow(["C-2", "6.8", "79.7", "6.81", "79.71"])
            with self.assertRaisesRegex(ValueError, "exceeds the maximum of 1"):
                worker.preflight_csv_file(path, detect_mapping(headers), max_rows=1)

    def test_stale_processing_run_is_requeued_once_and_keeps_uploaded_file(self):
        now = datetime.now(timezone.utc)
        old_progress = now - timedelta(hours=1)
        file_key = "b" * 32 + ".csv"
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(settings, "upload_dir", Path(temp)):
                upload_path = resolve_upload_path(file_key)
                upload_path.write_text("synthetic test fixture", encoding="utf-8")
                runs = FakeCollection([
                    {
                        "run_id": "retry-run",
                        "status": "PROCESSING",
                        "created_at": old_progress,
                        "processing_started_at": old_progress,
                        "last_progress_at": old_progress,
                        "input_file_key": file_key,
                        "attempt_count": 1,
                    }
                ])
                count = worker.mark_stale_processing_runs(runs, stale_after_seconds=60, max_attempts=2)
                updated = runs.find_one({"run_id": "retry-run"})
                self.assertEqual(count, 1)
                self.assertEqual(updated["status"], "QUEUED")
                self.assertTrue(updated["restart_from_beginning"])
                self.assertEqual(updated["input_file_key"], file_key)
                self.assertTrue(upload_path.exists())

    def test_processing_retry_clears_partial_results_before_replaying_file(self):
        headers = [
            "customer_id", "customer_latitude", "customer_longitude",
            "dp_latitude", "dp_longitude",
        ]
        file_key = "c" * 32 + ".csv"
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(settings, "upload_dir", Path(temp)):
                upload_path = resolve_upload_path(file_key)
                with upload_path.open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(headers)
                    writer.writerow(["SYNTHETIC-C1", "6.9", "79.8", "6.91", "79.81"])
                runs = FakeCollection([{
                    "run_id": "replay-run",
                    "status": "QUEUED",
                    "created_at": datetime.now(timezone.utc),
                    "input_file_key": file_key,
                    "column_mapping": detect_mapping(headers),
                    "rows_processed": 1,
                    "source_rows_seen": 1,
                    "issue_counts": {"OLD_PARTIAL_ISSUE": 1},
                    "attempt_count": 1,
                }])
                customers = FakeCollection([{"run_id": "replay-run", "row_number": 2, "customer_id": "OLD-PARTIAL"}])
                dps = FakeCollection([{"run_id": "replay-run", "dp_id": "OLD-PARTIAL-DP"}])

                def fake_process(**kwargs):
                    kwargs["validation_runs"].update_one(
                        {"run_id": "replay-run"},
                        {"$set": {"status": "COMPLETED", "rows_processed": 1, "summary": {"rows_processed": 1}}},
                    )
                    return {"run_id": "replay-run", "status": "COMPLETED", "rows_processed": 1}

                with patch.object(worker, "preflight_csv_file", return_value=1), patch.object(
                    worker, "process_csv_file", side_effect=fake_process
                ):
                    self.assertTrue(worker.process_next_queued_run(
                        run_collection=runs,
                        customer_collection=customers,
                        dp_collection=dps,
                        worker_id="worker-test",
                    ))

                self.assertEqual(customers.documents, [])
                self.assertEqual(dps.documents, [])
                updated = runs.find_one({"run_id": "replay-run"})
                self.assertEqual(updated["status"], "COMPLETED", updated.get("last_error"))
                self.assertEqual(updated["rows_processed"], 1)
                self.assertEqual(updated["issue_counts"], {})
                self.assertNotIn("input_file_key", updated)
                self.assertFalse(upload_path.exists())


if __name__ == "__main__":
    unittest.main()
