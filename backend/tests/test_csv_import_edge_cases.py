"""Regression tests for defensive CSV import behaviour."""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.validation.csv_import import (
    detect_mapping,
    iter_mapped_rows,
    preview_csv,
    validate_mapping,
)


HEADERS = [
    "customer_id",
    "customer_latitude",
    "customer_longitude",
    "dp_latitude",
    "dp_longitude",
]


def mapping():
    return detect_mapping(HEADERS)


class CSVImportEdgeCaseTests(unittest.TestCase):
    def test_rejects_missing_required_coordinate_mapping(self):
        with self.assertRaisesRegex(ValueError, "customer_longitude"):
            validate_mapping(
                HEADERS,
                {
                    "customer_latitude": "customer_latitude",
                    "dp_latitude": "dp_latitude",
                    "dp_longitude": "dp_longitude",
                },
            )

    def test_rejects_a_row_with_more_values_than_headers(self):
        source = io.StringIO(
            ",".join(HEADERS) + "\n"
            "C-1,6.9,79.8,6.91,79.81,unexpected\n"
        )
        with self.assertRaisesRegex(ValueError, "row 2 has more values than headers"):
            list(iter_mapped_rows(source, mapping()))

    def test_enforces_maximum_data_row_count(self):
        source = io.StringIO(
            ",".join(HEADERS) + "\n"
            "C-1,6.9,79.8,6.91,79.81\n"
            "C-2,6.8,79.7,6.81,79.71\n"
        )
        with self.assertRaisesRegex(ValueError, "exceeds the maximum of 1"):
            list(iter_mapped_rows(source, mapping(), max_rows=1))

    def test_rejects_oversized_cells(self):
        source = io.StringIO(
            ",".join(HEADERS) + "\n"
            "CUSTOMER-ID-TOO-LONG,6.9,79.8,6.91,79.81\n"
        )
        with self.assertRaisesRegex(ValueError, "cell over 8 characters"):
            list(iter_mapped_rows(source, mapping(), max_cell_chars=8))

    def test_preview_returns_header_mapping_and_small_sample(self):
        source = io.StringIO(
            ",".join(HEADERS) + "\n"
            "C-1,6.9,79.8,6.91,79.81\n"
            "C-2,6.8,79.7,6.81,79.71\n"
            "C-3,6.7,79.6,6.71,79.61\n"
        )
        preview = preview_csv(source, sample_size=1)
        self.assertEqual(preview["total_rows"], 3)
        self.assertEqual(len(preview["sample_rows"]), 1)
        self.assertEqual(preview["detected_mapping"]["customer_id"], "customer_id")

    def test_does_not_mix_customer_and_dp_coordinate_columns(self):
        headers = ["Customer Latitude", "Customer Longitude", "DP Latitude", "DP Longitude"]
        detected = detect_mapping(headers)
        self.assertEqual(detected["customer_latitude"], "Customer Latitude")
        self.assertEqual(detected["customer_longitude"], "Customer Longitude")
        self.assertEqual(detected["dp_latitude"], "DP Latitude")
        self.assertEqual(detected["dp_longitude"], "DP Longitude")

    def test_can_include_blank_rows_for_accurate_source_row_counts(self):
        source = io.StringIO(
            ",".join(HEADERS) + "\n"
            "C-1,6.9,79.8,6.91,79.81\n"
            ",,,,\n"
            "C-2,6.8,79.7,6.81,79.71\n"
        )
        rows = list(iter_mapped_rows(source, mapping(), include_blank_rows=True))
        self.assertEqual([row["row_number"] for row in rows], [2, 3, 4])
        self.assertTrue(rows[1]["is_blank"])
        self.assertFalse(rows[0]["is_blank"])

    def test_rejects_missing_header(self):
        with self.assertRaisesRegex(ValueError, "no header row"):
            list(iter_mapped_rows(io.StringIO(""), mapping()))


if __name__ == "__main__":
    unittest.main()
