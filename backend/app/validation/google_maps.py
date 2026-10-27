"""Optional, bounded Google Maps Routes and Geocoding API client.

The route API returns driving-road distance, not an overhead cable measurement.
Requests are opt-in, cached in memory for a run, and capped to avoid surprise usage.
"""
from __future__ import annotations

import threading
from typing import Any

import httpx

from .rules import haversine_m


class GoogleMapsClient:
    ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
    GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"

    def __init__(
        self,
        api_key: str,
        *,
        max_requests: int = 1000,
        timeout_seconds: float = 8.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("A Google Maps server API key is required.")
        if max_requests < 0:
            raise ValueError("max_requests must be non-negative.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self.api_key = api_key.strip()
        self.max_requests = max_requests
        self._requests_used = 0
        self._lock = threading.Lock()
        self._route_cache: dict[tuple[float, float, float, float], float | None] = {}
        self._geocode_cache: dict[str, tuple[float, float] | None] = {}
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = http_client is None

    @property
    def requests_used(self) -> int:
        with self._lock:
            return self._requests_used

    def _reserve_request(self) -> bool:
        with self._lock:
            if self._requests_used >= self.max_requests:
                return False
            self._requests_used += 1
            return True

    @staticmethod
    def _point_key(lat: float, lon: float) -> tuple[float, float]:
        return round(float(lat), 6), round(float(lon), 6)

    def route_distance_m(
        self, customer_lat: float, customer_lon: float, dp_lat: float, dp_lon: float
    ) -> float | None:
        """Return a driving-route distance in metres, or None when not verifiable."""
        customer = self._point_key(customer_lat, customer_lon)
        dp = self._point_key(dp_lat, dp_lon)
        key = (*customer, *dp)
        if key in self._route_cache:
            return self._route_cache[key]
        if not self._reserve_request():
            self._route_cache[key] = None
            return None

        payload = {
            "origin": {"location": {"latLng": {"latitude": customer[0], "longitude": customer[1]}}},
            "destination": {"location": {"latLng": {"latitude": dp[0], "longitude": dp[1]}}},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_UNAWARE",
        }
        try:
            response = self._client.post(
                self.ROUTES_URL,
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": "routes.distanceMeters",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data: Any = response.json()
            routes = data.get("routes") if isinstance(data, dict) else None
            distance = routes[0].get("distanceMeters") if routes else None
            result = float(distance) if isinstance(distance, (int, float)) and distance >= 0 else None
        except (httpx.HTTPError, ValueError, TypeError, IndexError, AttributeError):
            result = None
        self._route_cache[key] = result
        return result

    def _geocode(self, address: str) -> tuple[float, float] | None:
        normalized = " ".join(address.casefold().split())
        if normalized in self._geocode_cache:
            return self._geocode_cache[normalized]
        if not normalized or not self._reserve_request():
            self._geocode_cache[normalized] = None
            return None
        try:
            response = self._client.get(
                self.GEOCODING_URL,
                params={"address": address, "components": "country:LK", "key": self.api_key},
            )
            response.raise_for_status()
            data: Any = response.json()
            results = data.get("results") if isinstance(data, dict) else None
            location = results[0]["geometry"]["location"] if results else None
            lat, lon = float(location["lat"]), float(location["lng"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                point = None
            else:
                point = (lat, lon)
        except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, AttributeError):
            point = None
        self._geocode_cache[normalized] = point
        return point

    def address_distance_m(self, address: str, customer_lat: float, customer_lon: float) -> float | None:
        """Distance from the uploaded point to Google's resolved address point."""
        point = self._geocode(address)
        if point is None:
            return None
        return haversine_m(customer_lat, customer_lon, point[0], point[1])

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
