"""Synthetic polygon tests for optional, locally supplied reference datasets."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.reference_data import ReferenceData


def polygon_feature(name, level, bounds, **extra):
    min_lon, min_lat, max_lon, max_lat = bounds
    ring = [
        [min_lon, min_lat], [max_lon, min_lat],
        [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat],
    ]
    return {
        "type": "Feature",
        "properties": {"name": name, "level": level, **extra},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


class ReferenceDataTests(unittest.TestCase):
    def make_reference_directory(self, root: Path):
        (root / "country_boundary.geojson").write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [polygon_feature("Sample Country", "country", (79, 6, 81, 7))],
        }), encoding="utf-8")
        (root / "admin_areas.geojson").write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [
                polygon_feature("Sample Province", "province", (79, 6, 81, 7)),
                polygon_feature("Sample District", "district", (79, 6, 81, 7)),
                polygon_feature("Sample Town", "locality", (79, 6, 80, 7)),
            ],
        }), encoding="utf-8")
        (root / "lea_areas.geojson").write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [
                polygon_feature("Sample LEA", "lea", (79, 6, 80, 7), phone_prefix="011")
            ],
        }), encoding="utf-8")
        (root / "provider_regions.csv").write_text(
            "province,district,region\nSample Province,Sample District,Region-A\n",
            encoding="utf-8",
        )

    def test_loaded_polygons_and_mappings_validate_expected_sample(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_reference_directory(root)
            references = ReferenceData.load(root)
            self.assertTrue(references.country_check(6.5, 79.5))
            self.assertFalse(references.country_check(6.5, 81.5))
            self.assertTrue(references.address_zone_check("No 10 Sample Town Road", 6.5, 79.5))
            self.assertFalse(references.address_zone_check("Sample Town Main Road", 6.5, 80.5))
            self.assertTrue(references.phone_area_check("0111234567", 6.5, 79.5))
            self.assertFalse(references.phone_area_check("0111234567", 6.5, 80.5))
            self.assertIsNone(references.phone_area_check("0991234567", 6.5, 79.5))
            self.assertTrue(references.geography_check({
                "reported_province": "Sample Province",
                "reported_district": "Sample District",
                "reported_region": "Region-A",
            }, 6.5, 79.5))
            self.assertFalse(references.geography_check({
                "reported_province": "Wrong Province",
                "reported_district": "Sample District",
                "reported_region": "Region-A",
            }, 6.5, 79.5))
            status = references.status()
            self.assertTrue(status["country_boundary"]["configured"])
            self.assertEqual(len(status["country_boundary"]["sha256"]), 64)

    def test_missing_reference_files_remain_unverified_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as temp:
            references = ReferenceData.load(Path(temp))
            self.assertIsNone(references.country_check(6.5, 79.5))
            self.assertIsNone(references.address_zone_check("No 1 Sample Town Road", 6.5, 79.5))
            self.assertIsNone(references.phone_area_check("0111234567", 6.5, 79.5))
            self.assertIsNone(references.geography_check({
                "reported_province": "Sample Province",
            }, 6.5, 79.5))
            self.assertFalse(references.status()["country_boundary"]["configured"])

    def test_out_of_range_reference_geometry_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "country_boundary.geojson").write_text(json.dumps({
                "type": "FeatureCollection",
                "features": [polygon_feature("Invalid", "country", (79, 6, 190, 7))],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "out-of-range"):
                ReferenceData.load(root)


if __name__ == "__main__":
    unittest.main()
