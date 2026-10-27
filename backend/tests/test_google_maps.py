"""Offline tests for optional Google Maps client behavior."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.validation.google_maps import GoogleMapsClient


def fake_response(payload):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


class GoogleMapsClientTests(unittest.TestCase):
    def test_route_distance_parses_metric_distance_and_caches_repeats(self):
        http = Mock()
        http.post.return_value = fake_response({"routes": [{"distanceMeters": 321}]})
        client = GoogleMapsClient("test-key", max_requests=2, http_client=http)
        self.assertEqual(client.route_distance_m(6.9, 79.8, 6.91, 79.81), 321.0)
        self.assertEqual(client.route_distance_m(6.9, 79.8, 6.91, 79.81), 321.0)
        self.assertEqual(http.post.call_count, 1)
        args, kwargs = http.post.call_args
        self.assertEqual(args[0], GoogleMapsClient.ROUTES_URL)
        self.assertEqual(kwargs["headers"]["X-Goog-FieldMask"], "routes.distanceMeters")
        self.assertEqual(kwargs["json"]["travelMode"], "DRIVE")

    def test_request_limit_prevents_additional_route_calls(self):
        http = Mock()
        http.post.return_value = fake_response({"routes": [{"distanceMeters": 321}]})
        client = GoogleMapsClient("test-key", max_requests=1, http_client=http)
        self.assertEqual(client.route_distance_m(6.9, 79.8, 6.91, 79.81), 321.0)
        self.assertIsNone(client.route_distance_m(6.9, 79.8, 6.92, 79.82))
        self.assertEqual(http.post.call_count, 1)
        self.assertEqual(client.requests_used, 1)

    def test_route_without_a_result_is_unverified(self):
        http = Mock()
        http.post.return_value = fake_response({"routes": []})
        client = GoogleMapsClient("test-key", max_requests=1, http_client=http)
        self.assertIsNone(client.route_distance_m(6.9, 79.8, 6.91, 79.81))

    def test_geocoded_address_returns_distance_from_uploaded_point(self):
        http = Mock()
        http.get.return_value = fake_response({
            "status": "OK",
            "results": [{"geometry": {"location": {"lat": 6.9272, "lng": 79.8613}}}],
        })
        client = GoogleMapsClient("test-key", max_requests=2, http_client=http)
        distance = client.address_distance_m("Synthetic address, Sri Lanka", 6.9272, 79.8613)
        self.assertIsNotNone(distance)
        self.assertAlmostEqual(distance, 0.0, places=4)
        self.assertEqual(http.get.call_count, 1)
        args, kwargs = http.get.call_args
        self.assertEqual(args[0], GoogleMapsClient.GEOCODING_URL)
        self.assertEqual(kwargs["params"]["components"], "country:LK")

    def test_invalid_key_and_limits_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "API key"):
            GoogleMapsClient("")
        with self.assertRaisesRegex(ValueError, "non-negative"):
            GoogleMapsClient("test-key", max_requests=-1)
