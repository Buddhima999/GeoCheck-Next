import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.validation.csv_import import detect_mapping, iter_mapped_rows, validate_mapping
from app.validation.rules import (
    FAIL, NOT_APPLICABLE, PASS, UNVERIFIED, ValidationSettings,
    haversine_m, validate_dataset_small, validate_row,
)


class CSVImportTests(unittest.TestCase):
    def test_detects_sample_headers(self):
        headers = [
            "customer_id", "phone_number", "address", "customer_latitude",
            "customer_longitude", "dp_id", "dp_latitude", "dp_longitude",
            "line_distance_m", "overhead_distance_m", "reported_province",
            "reported_district", "reported_region",
        ]
        mapping = detect_mapping(headers)
        self.assertEqual(mapping["customer_id"], "customer_id")
        self.assertEqual(mapping["customer_latitude"], "customer_latitude")
        self.assertEqual(mapping["dp_longitude"], "dp_longitude")
        self.assertEqual(mapping["line_distance_m"], "line_distance_m")

    def test_streams_rows_and_preserves_source_row_numbers(self):
        source = io.StringIO(
            "customer_id,customer_latitude,customer_longitude,dp_latitude,dp_longitude\n"
            "A,6.9,79.8,6.91,79.81\nB,6.8,79.7,6.81,79.71\n"
        )
        headers = ["customer_id", "customer_latitude", "customer_longitude", "dp_latitude", "dp_longitude"]
        rows = list(iter_mapped_rows(source, detect_mapping(headers)))
        self.assertEqual([row["row_number"] for row in rows], [2, 3])
        self.assertEqual(rows[0]["mapped"]["customer_id"], "A")

    def test_rejects_duplicate_headers(self):
        with self.assertRaises(ValueError):
            validate_mapping(
                ["customer latitude", "customer_latitude", "customer_longitude", "dp_latitude", "dp_longitude"],
                {
                    "customer_latitude": "customer latitude",
                    "customer_longitude": "customer_longitude",
                    "dp_latitude": "dp_latitude",
                    "dp_longitude": "dp_longitude",
                },
            )


class RuleTests(unittest.TestCase):
    def good_row(self):
        return {
            "row_number": 2,
            "customer_id": "C-1", "phone_number": "0111234567", "address": "Synthetic road, Colombo",
            "customer_latitude": "6.9272", "customer_longitude": "79.8613",
            "dp_id": "DP-1", "dp_latitude": "6.9271", "dp_longitude": "79.8612",
            "line_distance_m": "120", "overhead_distance_m": "15",
            "reported_province": "Western", "reported_district": "Colombo", "reported_region": "SYN-1",
        }

    def test_haversine_zero_and_known_distance(self):
        self.assertAlmostEqual(haversine_m(6.9, 79.8, 6.9, 79.8), 0, places=6)
        self.assertGreater(haversine_m(6.9, 79.8, 6.91, 79.8), 1000)

    def test_missing_and_non_numeric_coordinates(self):
        row = self.good_row()
        row["customer_latitude"] = ""
        result = validate_row(row)
        self.assertIn("COORD_MISSING", result["issue_codes"])
        self.assertEqual(result["checks"]["customer_dp_distance"]["status"], NOT_APPLICABLE)
        row = self.good_row()
        row["customer_latitude"] = "NOT_A_NUMBER"
        self.assertIn("COORD_NOT_NUMERIC", validate_row(row)["issue_codes"])

    def test_out_of_range_zero_and_possible_swap(self):
        row = self.good_row()
        row["customer_latitude"] = "120"
        self.assertIn("COORD_OUT_OF_RANGE", validate_row(row)["issue_codes"])
        row = self.good_row()
        row["customer_latitude"], row["customer_longitude"] = "0", "0"
        self.assertIn("COORD_ZERO_PLACEHOLDER", validate_row(row)["issue_codes"])
        row = self.good_row()
        row["customer_latitude"], row["customer_longitude"] = "79.8612", "6.9271"
        result = validate_row(row)
        self.assertIn("COORD_POSSIBLE_SWAP", result["issue_codes"])
        self.assertNotIn("CUSTOMER_DP_DISTANCE", result["issue_codes"])
        self.assertEqual(result["checks"]["country_boundary"]["status"], UNVERIFIED)

    def test_reference_checks_are_unverified_if_not_configured(self):
        result = validate_row(self.good_row())
        self.assertEqual(result["checks"]["country_boundary"]["status"], UNVERIFIED)
        self.assertEqual(result["checks"]["address_zone"]["status"], UNVERIFIED)
        self.assertEqual(result["checks"]["phone_area"]["status"], UNVERIFIED)
        self.assertEqual(result["checks"]["reported_geography"]["status"], UNVERIFIED)

    def test_distance_and_negative_line_distance(self):
        row = self.good_row()
        row["customer_latitude"], row["customer_longitude"] = "7.31", "80.65"
        self.assertIn("CUSTOMER_DP_DISTANCE", validate_row(row)["issue_codes"])
        row = self.good_row()
        row["line_distance_m"] = "-25"
        result = validate_row(row)
        self.assertIn("LINE_DISTANCE_INVALID", result["issue_codes"])
        self.assertEqual(result["checks"]["line_distance"]["status"], FAIL)

    def test_overhead_mismatch_is_enabled_by_default(self):
        row = self.good_row()
        row["overhead_distance_m"] = "999"
        result = validate_row(row)
        self.assertIn("OVERHEAD_DISTANCE_MISMATCH", result["issue_codes"])
        self.assertEqual(result["checks"]["overhead_distance"]["status"], FAIL)

    def test_overhead_comparison_can_be_disabled_explicitly(self):
        from app.validation.rules import ValidationSettings

        row = self.good_row()
        row["overhead_distance_m"] = "999"
        result = validate_row(row, settings=ValidationSettings(overhead_distance_is_straight_line=False))
        self.assertNotIn("OVERHEAD_DISTANCE_MISMATCH", result["issue_codes"])
        self.assertEqual(result["checks"]["overhead_distance"]["status"], UNVERIFIED)

    def test_reported_line_distance_is_not_given_a_fake_500m_cable_limit(self):
        row = self.good_row()
        row["line_distance_m"] = "9000"
        result = validate_row(row)
        self.assertNotIn("LINE_DISTANCE_LIMIT", result["issue_codes"])
        self.assertEqual(result["checks"]["line_distance"]["status"], PASS)

    def test_google_routes_distance_is_used_for_the_500m_rule_when_configured(self):
        row = self.good_row()
        row["router_number"] = "ROUTER-001"
        result = validate_row(
            row,
            settings=ValidationSettings(distance_method="google_routes", max_line_distance_m=None),
            country_check=lambda lat, lon: True,
            route_distance_check=lambda c_lat, c_lon, d_lat, d_lon: 700.0,
        )
        self.assertEqual(result["checks"]["google_route_distance"]["status"], FAIL)
        self.assertEqual(result["checks"]["customer_dp_distance"]["distance_m"], 700.0)
        self.assertEqual(result["checks"]["customer_dp_distance"]["method"], "google_routes")
        self.assertIn("CUSTOMER_DP_DISTANCE", result["issue_codes"])
        # Haversine remains a separate measurement and is never overwritten.
        self.assertLess(result["calculated_customer_dp_distance_m"], 500.0)

    def test_google_routes_unavailable_stays_unverified_without_straight_line_fallback(self):
        row = self.good_row()
        result = validate_row(
            row,
            settings=ValidationSettings(distance_method="google_routes", max_line_distance_m=None),
            country_check=lambda lat, lon: True,
            route_distance_check=lambda c_lat, c_lon, d_lat, d_lon: None,
        )
        self.assertEqual(result["checks"]["customer_dp_distance"]["status"], UNVERIFIED)
        self.assertEqual(result["checks"]["google_route_distance"]["status"], UNVERIFIED)
        self.assertIn("CUSTOMER_DP_ROUTE_UNVERIFIED", result["issue_codes"])
        self.assertNotIn("CUSTOMER_DP_DISTANCE", result["issue_codes"])

    def test_google_geocoded_address_distance_is_checked_against_tolerance(self):
        row = self.good_row()
        row["router_number"] = "ROUTER-001"
        result = validate_row(
            row,
            country_check=lambda lat, lon: True,
            geocode_address_check=lambda address, lat, lon: 900.0,
        )
        self.assertEqual(result["checks"]["address_zone"]["status"], FAIL)
        self.assertIn("ADDRESS_LOCATION_MISMATCH", result["issue_codes"])

    def test_google_geocoded_address_can_pass_within_tolerance(self):
        row = self.good_row()
        row["router_number"] = "ROUTER-001"
        result = validate_row(
            row,
            country_check=lambda lat, lon: True,
            geocode_address_check=lambda address, lat, lon: 25.0,
        )
        self.assertEqual(result["checks"]["address_zone"]["status"], PASS)
        self.assertNotIn("ADDRESS_LOCATION_MISMATCH", result["issue_codes"])

    def test_cross_row_duplicate_and_dp_coordinate_conflicts(self):
        first = self.good_row()
        second = self.good_row()
        second["row_number"] = 3
        second["customer_id"] = "C-1"
        second["customer_latitude"], second["customer_longitude"] = "7.2", "80.6"
        third = self.good_row()
        third["row_number"] = 4
        third["customer_id"] = "C-2"
        third["dp_latitude"], third["dp_longitude"] = "7.2", "80.6"
        results = validate_dataset_small([first, second, third])
        self.assertIn("CUSTOMER_ID_CONFLICT", results[0]["issue_codes"])
        self.assertIn("CUSTOMER_ID_CONFLICT", results[1]["issue_codes"])
        self.assertIn("DP_COORD_CONFLICT", results[0]["issue_codes"])
        self.assertIn("DP_COORD_CONFLICT", results[2]["issue_codes"])

    def test_reference_callbacks_can_confirm_mismatches(self):
        result = validate_row(
            self.good_row(),
            address_zone_check=lambda address, lat, lon: False,
            phone_area_check=lambda phone, lat, lon: False,
            country_check=lambda lat, lon: True,
            geography_check=lambda values, lat, lon: False,
        )
        self.assertIn("ADDRESS_ZONE_MISMATCH", result["issue_codes"])
        self.assertIn("PHONE_AREA_MISMATCH", result["issue_codes"])
        self.assertIn("REPORTED_GEOGRAPHY_MISMATCH", result["issue_codes"])
        self.assertEqual(result["checks"]["country_boundary"]["status"], PASS)


if __name__ == "__main__":
    unittest.main()
