"""Smoke-test the synthetic fixture generator without persisting large data."""
from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "test-data" / "generate_synthetic_csv.py"


class SyntheticGeneratorTests(unittest.TestCase):
    def test_generates_50_rows_and_documented_scenarios(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            csv_path = output / "synthetic.csv"
            expected_path = output / "expected.csv"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR),
                    "--rows",
                    "50",
                    "--dp-count",
                    "15",
                    "--csv-output",
                    str(csv_path),
                    "--expected-output",
                    str(expected_path),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("Generated 50 synthetic customer rows", completed.stdout)
            with csv_path.open(newline="", encoding="utf-8") as handle:
                customer_rows = list(csv.DictReader(handle))
            with expected_path.open(newline="", encoding="utf-8") as handle:
                expected_rows = list(csv.DictReader(handle))

            self.assertEqual(len(customer_rows), 50)
            self.assertEqual(len(expected_rows), 14)
            self.assertEqual(customer_rows[0]["customer_latitude"], "")
            self.assertEqual(expected_rows[0]["expected_issue_code"], "COORD_MISSING")
            self.assertEqual(expected_rows[-1]["expected_issue_code"], "DP_COORD_CONFLICT")


if __name__ == "__main__":
    unittest.main()
