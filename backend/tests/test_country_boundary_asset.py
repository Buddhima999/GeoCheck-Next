"""Smoke tests for the country boundary bundled from the legacy project."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.reference_data import ReferenceData


class SriLankaBoundaryTests(unittest.TestCase):
    def test_colombo_point_is_inside_boundary(self):
        references = ReferenceData.load(ROOT / "reference-data")
        self.assertIs(references.country_check(6.9272, 79.8613), True)

    def test_point_far_outside_sri_lanka_is_outside_boundary(self):
        references = ReferenceData.load(ROOT / "reference-data")
        self.assertIs(references.country_check(40.7128, -74.0060), False)


if __name__ == "__main__":
    unittest.main()
