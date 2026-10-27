"""Unit tests for bounded-batch CSV processing, using small in-memory collection fakes."""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.validation.batch_processor import process_csv_file
from app.validation.csv_import import detect_mapping


HEADERS = [
    "customer_id", "phone_number", "address",
    "customer_latitude", "customer_longitude",
    "dp_id", "dp_latitude", "dp_longitude",
    "line_distance_m", "overhead_distance_m",
    "reported_province", "reported_district", "reported_region",
]


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = [dict(document) for document in (documents or [])]

    @staticmethod
    def _matches(document, query):
        for key, value in query.items():
            actual = document.get(key)
            if isinstance(value, dict) and "$in" in value:
                if actual not in value["$in"]:
                    return False
            elif actual != value:
                return False
        return True

    @staticmethod
    def _set_path(document, key, value):
        parts = key.split(".")
        current = document
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    @staticmethod
    def _get_path(document, key):
        current = document
        for part in key.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    @classmethod
    def _apply_update(cls, document, update, inserting=False):
        if inserting:
            for key, value in update.get("$setOnInsert", {}).items():
                cls._set_path(document, key, value)
        for key, value in update.get("$set", {}).items():
            cls._set_path(document, key, value)
        for key, value in update.get("$inc", {}).items():
            cls._set_path(document, key, (cls._get_path(document, key) or 0) + value)
        for key, value in update.get("$addToSet", {}).items():
            current = cls._get_path(document, key)
            if current is None:
                current = []
                cls._set_path(document, key, current)
            values = value.get("$each", []) if isinstance(value, dict) and "$each" in value else [value]
            for item in values:
                if item not in current:
                    current.append(item)

    def find(self, query, projection=None):
        return [dict(document) for document in self.documents if self._matches(document, query)]

    def insert_many(self, documents, ordered=True):
        self.documents.extend(dict(document) for document in documents)
        return {"inserted_count": len(documents)}

    def update_one(self, query, update, upsert=False):
        for document in self.documents:
            if self._matches(document, query):
                self._apply_update(document, update, inserting=False)
                return
        if upsert:
            document = dict(query)
            self._apply_update(document, update, inserting=True)
            self.documents.append(document)

    def bulk_write(self, operations, ordered=False):
        for operation in operations:
            query = operation._filter
            update = operation._doc
            found = None
            for document in self.documents:
                if self._matches(document, query):
                    found = document
                    break
            if found is None and operation._upsert:
                found = dict(query)
                self._apply_update(found, update, inserting=True)
                self.documents.append(found)
            elif found is not None:
                self._apply_update(found, update, inserting=False)


class BatchProcessorTests(unittest.TestCase):
    def make_row(self, customer_id, *, customer_lat="6.9001", customer_lon="79.8501",
                 dp_id="DP-A", dp_lat="6.9", dp_lon="79.85"):
        return {
            "customer_id": customer_id,
            "phone_number": "0710000000",
            "address": "Synthetic test address",
            "customer_latitude": customer_lat,
            "customer_longitude": customer_lon,
            "dp_id": dp_id,
            "dp_latitude": dp_lat,
            "dp_longitude": dp_lon,
            "line_distance_m": "120",
            "overhead_distance_m": "20",
            "reported_province": "SYNTHETIC",
            "reported_district": "SYNTHETIC",
            "reported_region": "SYNTHETIC",
        }

    def run_rows(self, rows, *, batch_size=2):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        csv_path = Path(temp.name) / "input.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerows(rows)

        runs = FakeCollection([{"run_id": "run-test", "status": "QUEUED"}])
        customers = FakeCollection()
        dps = FakeCollection()
        result = process_csv_file(
            csv_path,
            run_id="run-test",
            mapping=detect_mapping(HEADERS),
            validation_runs=runs,
            customer_records=customers,
            dp_records=dps,
            batch_size=batch_size,
        )
        return result, runs, customers, dps

    def test_persists_rows_in_bounded_batches_and_completes_run(self):
        rows = [self.make_row(f"C-{index}") for index in range(5)]
        result, runs, customers, dps = self.run_rows(rows, batch_size=2)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["rows_processed"], 5)
        self.assertEqual(result["batches_processed"], 3)
        self.assertEqual(len(customers.documents), 5)
        self.assertEqual(len(dps.documents), 1)
        self.assertEqual(runs.documents[0]["status"], "COMPLETED")
        self.assertEqual(dps.documents[0]["observed_row_count"], 5)

    def test_dp_coordinate_conflict_is_saved_at_dp_entity_level(self):
        rows = [
            self.make_row("C-1", dp_lat="6.9", dp_lon="79.85"),
            self.make_row("C-2", dp_lat="7.0", dp_lon="79.85"),
        ]
        result, _, _, dps = self.run_rows(rows, batch_size=1)
        self.assertEqual(result["rows_processed"], 2)
        self.assertEqual(len(dps.documents), 1)
        self.assertEqual(dps.documents[0]["coordinate_consistency"], "CONFLICT")
        self.assertIn("DP_COORD_CONFLICT", dps.documents[0]["issue_codes"])
        self.assertEqual(dps.documents[0]["observed_row_count"], 2)

    def test_customer_with_invalid_location_is_not_eligible_for_dp_assessment(self):
        rows = [self.make_row("C-1", customer_lat="")]
        _, _, customers, dps = self.run_rows(rows)
        self.assertFalse(customers.documents[0]["eligible_for_dp_assessment"])
        self.assertEqual(dps.documents[0]["eligible_customer_row_count"], 0)

    def test_blank_csv_record_is_counted_but_not_validated_as_customer(self):
        rows = [self.make_row("C-1"), {key: "" for key in HEADERS}, self.make_row("C-2")]
        result, _, customers, dps = self.run_rows(rows, batch_size=2)
        self.assertEqual(result["source_rows_seen"], 3)
        self.assertEqual(result["blank_rows_skipped"], 1)
        self.assertEqual(result["rows_processed"], 2)
        self.assertEqual(len(customers.documents), 2)
        self.assertEqual(dps.documents[0]["observed_row_count"], 2)

    def test_invalid_batch_size_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "empty.csv"
            path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "batch_size"):
                process_csv_file(
                    path,
                    run_id="run-test",
                    mapping=detect_mapping(HEADERS),
                    validation_runs=FakeCollection([{"run_id": "run-test"}]),
                    customer_records=FakeCollection(),
                    dp_records=FakeCollection(),
                    batch_size=0,
                )


if __name__ == "__main__":
    unittest.main()
