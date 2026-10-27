"""API tests use synthetic CSV content and in-memory Mongo collection fakes."""
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
from app.api import validation_runs as api
from app.config import settings


class FakeCollection:
    def __init__(self):
        self.documents: list[dict] = []

    def insert_one(self, document):
        self.documents.append(dict(document))
        return type("InsertResult", (), {"inserted_id": document.get("run_id")})()

    def find_one(self, query, projection=None):
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return dict(document)
        return None


CSV_BYTES = (
    b"customer_id,customer_latitude,customer_longitude,dp_latitude,dp_longitude\n"
    b"SYNTHETIC-C-1,6.9,79.8,6.91,79.81\n"
)


class ValidationUploadApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.upload_dir = Path(self.temp.name)
        self.runs = FakeCollection()
        self.patches = [
            patch.object(api, "validation_runs", self.runs),
            patch.object(settings, "api_key", "test-only-secret"),
            patch.object(settings, "upload_dir", self.upload_dir),
            patch.object(settings, "max_upload_bytes", 1024),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.headers = {"X-API-Key": "test-only-secret"}

    def tearDown(self):
        self.temp.cleanup()

    def upload(self, content=CSV_BYTES, filename="customers.csv", headers=None, data=None):
        return self.client.post(
            "/api/validation-runs",
            headers=headers or self.headers,
            files={"file": (filename, content, "text/csv")},
            data=data or {},
        )

    def test_upload_is_queued_and_does_not_return_customer_rows(self):
        response = self.upload()
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["status"], "QUEUED")
        self.assertEqual(body["source_rows_seen"], 0)
        self.assertEqual(body["column_mapping"]["customer_latitude"], "customer_latitude")
        self.assertNotIn("input_file_key", body)
        self.assertEqual(len(self.runs.documents), 1)
        stored = self.runs.documents[0]
        self.assertTrue((self.upload_dir / stored["input_file_key"]).is_file())
        self.assertEqual(stored["attempt_count"], 0)

        status_response = self.client.get(
            f"/api/validation-runs/{body['run_id']}",
            headers=self.headers,
        )
        self.assertEqual(status_response.status_code, 200, status_response.text)
        self.assertEqual(status_response.json()["status"], "QUEUED")
        self.assertNotIn("input_file_key", status_response.json())

    def test_upload_endpoint_fails_closed_when_api_key_is_not_configured(self):
        with patch.object(settings, "api_key", None):
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(self.runs.documents), 0)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_upload_requires_the_correct_api_key(self):
        response = self.upload(headers={"X-API-Key": "incorrect"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(self.runs.documents), 0)

    def test_rejects_non_csv_extension(self):
        response = self.upload(filename="customers.xlsx")
        self.assertEqual(response.status_code, 415)
        self.assertEqual(len(self.runs.documents), 0)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_rejects_upload_over_configured_limit_and_removes_partial_file(self):
        with patch.object(settings, "max_upload_bytes", 20):
            response = self.upload()
        self.assertEqual(response.status_code, 413)
        self.assertEqual(len(self.runs.documents), 0)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_rejects_csv_without_all_required_coordinate_headers(self):
        bad_csv = b"customer_id,customer_latitude,customer_longitude\nC-1,6.9,79.8\n"
        response = self.upload(content=bad_csv)
        self.assertEqual(response.status_code, 400)
        self.assertIn("dp_latitude", response.json()["detail"])
        self.assertEqual(len(self.runs.documents), 0)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_rejects_unknown_custom_mapping_field(self):
        response = self.upload(data={"column_mapping": '{"customer_latitude":"customer_latitude","unknown":"id"}'})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported logical field", response.json()["detail"])
        self.assertEqual(len(self.runs.documents), 0)
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    def test_unknown_run_returns_404(self):
        response = self.client.get(
            "/api/validation-runs/no-such-run",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404)

    def test_preview_detects_headers_without_creating_a_validation_run(self):
        response = self.client.post(
            "/api/validation-runs/preview",
            headers=self.headers,
            files={"file": ("synthetic.csv", CSV_BYTES, "text/csv")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["headers"][0], "customer_id")
        self.assertEqual(body["detected_mapping"]["customer_latitude"], "customer_latitude")
        self.assertIn("customer_latitude", body["required_fields"])
        self.assertEqual(len(self.runs.documents), 0)

    def test_preview_keeps_quoted_commas_inside_one_header(self):
        content = (
            b'customer_id,"address, full",customer_latitude,customer_longitude,dp_latitude,dp_longitude\n'
            b'SYN-C-1,"Synthetic road",6.9,79.8,6.91,79.81\n'
        )
        response = self.client.post(
            "/api/validation-runs/preview",
            headers=self.headers,
            files={"file": ("synthetic.csv", content, "text/csv")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["headers"][1], "address, full")

    def test_preview_rejects_duplicate_headers(self):
        content = (
            b"customer_latitude,Customer Latitude,customer_longitude,dp_latitude,dp_longitude\n"
            b"6.9,6.9,79.8,6.91,79.81\n"
        )
        response = self.client.post(
            "/api/validation-runs/preview",
            headers=self.headers,
            files={"file": ("synthetic.csv", content, "text/csv")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("duplicate", response.json()["detail"].lower())

    def test_preview_requires_api_key(self):
        response = self.client.post(
            "/api/validation-runs/preview",
            files={"file": ("synthetic.csv", CSV_BYTES, "text/csv")},
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
