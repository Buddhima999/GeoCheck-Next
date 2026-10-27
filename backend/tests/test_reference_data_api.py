"""API coverage for the protected reference inventory endpoint."""
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
from app.api import reference_data as reference_api
from app.config import settings
from app.reference_data import ReferenceData


class ReferenceDataApiTests(unittest.TestCase):
    def test_status_endpoint_reports_missing_references_without_exposing_local_path(self):
        with tempfile.TemporaryDirectory() as temp:
            references = ReferenceData.load(Path(temp))
            with patch.object(settings, "api_key", "test-only-secret"), patch.object(
                reference_api.ReferenceData, "load", return_value=references
            ):
                client = TestClient(main.app)
                try:
                    response = client.get(
                        "/api/reference-data/status",
                        headers={"X-API-Key": "test-only-secret"},
                    )
                finally:
                    client.close()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["country_boundary"]["configured"])
        self.assertEqual(body["country_boundary"]["status"], "MISSING")
        self.assertNotIn("directory", body)
        self.assertTrue(body["directory_configured"])

    def test_status_endpoint_requires_api_key(self):
        with patch.object(settings, "api_key", "test-only-secret"):
            client = TestClient(main.app)
            try:
                response = client.get("/api/reference-data/status")
            finally:
                client.close()
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
