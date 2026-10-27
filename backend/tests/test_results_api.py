"""Tests for run listing, result searches, summaries and streaming exports."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import main
from app.api import results as result_api
from app.config import settings


def get_path(document, path):
    current = document
    for part in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def matches(document, query):
    for key, expected in query.items():
        if key == "$and":
            if not all(matches(document, part) for part in expected):
                return False
            continue
        if key == "$or":
            if not any(matches(document, part) for part in expected):
                return False
            continue
        actual = get_path(document, key)
        if isinstance(expected, dict):
            if "$regex" in expected:
                import re
                flags = re.IGNORECASE if expected.get("$options") == "i" else 0
                if actual is None or re.search(expected["$regex"], str(actual), flags) is None:
                    return False
            if "$exists" in expected and ((actual is not None) != bool(expected["$exists"])):
                return False
        elif isinstance(actual, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, documents):
        self.documents = list(documents)

    def sort(self, field, direction=1):
        self.documents.sort(key=lambda doc: get_path(doc, field) or "", reverse=direction < 0)
        return self

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def batch_size(self, count):
        return self

    def __iter__(self):
        return iter(self.documents)


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = [dict(document) for document in (documents or [])]

    def find_one(self, query, projection=None):
        return next((dict(doc) for doc in self.documents if matches(doc, query)), None)

    def count_documents(self, query):
        return sum(matches(doc, query) for doc in self.documents)

    def find(self, query, projection=None):
        return FakeCursor([dict(doc) for doc in self.documents if matches(doc, query)])

    def aggregate(self, pipeline, allowDiskUse=False):
        raise AssertionError("Aggregation is covered by deployment/integration tests, not this fake.")


class ValidationResultsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runs = FakeCollection([
            {
                "run_id": "run-1", "status": "COMPLETED", "created_at": "2026-10-27T00:00:00Z",
                "original_filename": "synthetic.csv", "source_rows_seen": 2,
                "blank_rows_skipped": 0, "rows_processed": 2,
                "rows_with_findings": 1, "rows_with_hard_errors": 1,
                "issue_counts": {"CUSTOMER_DP_DISTANCE": 1, "COORD_MISSING": 1},
                "summary": {"rows_processed": 2, "finding_count": 2},
            }
        ])
        self.customers = FakeCollection([
            {
                "run_id": "run-1", "row_number": 2, "customer_id": "SYN-C-1",
                "dp_id": "DP-1", "source_values": {
                    "address": "Synthetic street", "phone_number": "0710000000",
                    "reported_province": "Synthetic province",
                },
                "customer_location": {"lat": 6.9, "lon": 79.8},
                "dp_location_observed": {"lat": 6.91, "lon": 79.81},
                "issue_codes": ["CUSTOMER_DP_DISTANCE"], "findings": [],
                "has_hard_error": False, "eligible_for_dp_assessment": True,
            },
            {
                "run_id": "run-1", "row_number": 3, "customer_id": "SYN-C-2",
                "dp_id": "DP-1", "source_values": {"address": "Other synthetic street"},
                "customer_location": None, "dp_location_observed": {"lat": 6.91, "lon": 79.81},
                "issue_codes": ["COORD_MISSING"], "findings": [],
                "has_hard_error": True, "eligible_for_dp_assessment": False,
            },
        ])
        self.dps = FakeCollection([
            {
                "run_id": "run-1", "dp_id": "DP-1", "dp_id_normalized": "dp-1",
                "dp_location_observed": {"lat": 6.91, "lon": 79.81},
                "coordinate_consistency": "CONFLICT", "observed_row_count": 2,
                "valid_coordinate_observation_count": 2, "coordinate_conflict_observation_count": 1,
                "eligible_customer_row_count": 1, "eligible_distance_fail_row_count": 1,
                "issue_codes": ["DP_COORD_CONFLICT"],
            }
        ])
        self.patches = [
            patch.object(result_api, "validation_runs", self.runs),
            patch.object(result_api, "customer_records", self.customers),
            patch.object(result_api, "dp_records", self.dps),
            patch.object(settings, "api_key", "test-only-secret"),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-API-Key": "test-only-secret"}

    def test_lists_validation_runs_with_pagination(self):
        response = self.client.get("/api/validation-runs?limit=10", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["run_id"], "run-1")

    def test_customer_search_and_issue_filter(self):
        response = self.client.get(
            "/api/validation-runs/run-1/customers?q=SYN-C-2&issue_code=COORD_MISSING",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["customer_id"], "SYN-C-2")

    def test_customer_search_rejects_unbounded_query(self):
        response = self.client.get(
            "/api/validation-runs/run-1/customers?q=" + ("x" * 101),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 422)

    def test_dp_search_filters_coordinate_conflicts(self):
        response = self.client.get(
            "/api/validation-runs/run-1/dps?consistency=CONFLICT",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["dp_id"], "DP-1")

    def test_summary_uses_persisted_counts(self):
        response = self.client.get("/api/validation-runs/run-1/summary", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["customer_rows_stored"], 2)
        self.assertEqual(body["rows_with_findings"], 2)
        self.assertEqual(body["rows_with_hard_errors"], 1)
        self.assertEqual(body["eligible_for_dp_assessment"], 1)
        self.assertEqual(body["dp_coordinate_conflict"], 1)

    def test_customer_export_streams_rows_and_spreadsheet_safe_values(self):
        self.customers.documents[0]["customer_id"] = "=HYPERLINK(1)"
        response = self.client.get(
            "/api/validation-runs/run-1/customers/export.csv",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn("'=HYPERLINK(1)", response.text)
        self.assertIn("SYN-C-2", response.text)

    def test_unknown_run_is_404(self):
        response = self.client.get("/api/validation-runs/missing/summary", headers=self.headers)
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
