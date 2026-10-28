"""Optional, cached town-boundary lookup using only the address's final locality token.

Public Nominatim lookup is disabled by default. When enabled, one worker selects
the most common towns, queries them serially at <= 1 request/second, and stores
the returned polygons in MongoDB for reuse across runs. Full addresses, phone
numbers, IDs, and coordinates are never sent to Nominatim.
"""
from __future__ import annotations

import re
import threading
import time
from collections import Counter
from collections.abc import Mapping
from typing import Any
from urllib.parse import urljoin

import httpx
from pymongo.collection import Collection

from ..reference_data import _geometry_contains
from .csv_import import iter_mapped_rows

_ROAD_TERMS = {"road", "rd", "street", "st", "lane", "ln", "avenue", "ave", "highway", "junction"}
_ALLOWED_TYPES = {"city", "town", "village", "municipality", "administrative", "suburb", "locality"}
_request_lock = threading.Lock()
_last_request_at = 0.0


def town_from_address(address: object) -> str | None:
    """Return a conservative final comma-delimited locality, never the full address."""
    parts = [part.strip() for part in str(address or "").split(",") if part.strip()]
    if not parts:
        return None
    town = parts[-1]
    if town.casefold() in {"sri lanka", "lk"} and len(parts) > 1:
        town = parts[-2]
    town = re.sub(r"[^\w .'-]", " ", town, flags=re.UNICODE).strip(" .'-")
    words = [word.casefold() for word in town.split()]
    if (not town or len(town) > 80 or any(ch.isdigit() for ch in town)
            or any(word in _ROAD_TERMS for word in words)):
        return None
    return re.sub(r"\s+", " ", town)


class NominatimTownZoneClient:
    def __init__(
        self,
        cache: Collection,
        *,
        base_url: str,
        email: str,
        max_lookups: int = 500,
        timeout_seconds: float = 12.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not email.strip():
            raise ValueError("OSM_NOMINATIM_EMAIL must identify the operator when town lookups are enabled.")
        if max_lookups < 0:
            raise ValueError("max_lookups must be non-negative.")
        self.cache = cache
        self.base_url = base_url.rstrip("/") + "/"
        self.email = email.strip()
        self.max_lookups = max_lookups
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = http_client is None
        self._eligible_towns: set[str] = set()
        self._queries_used = 0

    @property
    def queries_used(self) -> int:
        return self._queries_used

    def prepare(self, csv_path: str, mapping: Mapping[str, str], *, max_rows: int) -> int:
        """Choose the most common distinct towns before validation begins."""
        counts: Counter[str] = Counter()
        with open(csv_path, "r", newline="", encoding="utf-8-sig") as stream:
            for row in iter_mapped_rows(stream, mapping, include_blank_rows=False, max_rows=max_rows):
                town = town_from_address((row.get("mapped") or {}).get("address"))
                if town:
                    counts[town.casefold()] += 1
        ranked = sorted(counts, key=lambda town: (-counts[town], town))[: self.max_lookups]
        self._eligible_towns = set(ranked)
        return len(ranked)

    def _lookup(self, town_key: str) -> dict[str, Any] | None:
        cached = self.cache.find_one({"_id": town_key}, {"geometry": 1, "lookup_status": 1})
        if cached:
            return cached if cached.get("lookup_status") == "FOUND" else None

        global _last_request_at
        with _request_lock:
            delay = max(0.0, 1.05 - (time.monotonic() - _last_request_at))
            if delay:
                time.sleep(delay)
            _last_request_at = time.monotonic()
            self._queries_used += 1
            try:
                response = self._client.get(
                    urljoin(self.base_url, "search"),
                    params={
                        "q": f"{town_key}, Sri Lanka",
                        "format": "jsonv2",
                        "addressdetails": 1,
                        "polygon_geojson": 1,
                        "polygon_threshold": 0.002,
                        "limit": 1,
                        "countrycodes": "lk",
                        "email": self.email,
                    },
                    headers={"User-Agent": f"GeoCheck Next/0.2 (contact: {self.email})"},
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError, TypeError):
                return None

        if not isinstance(payload, list) or not payload:
            self.cache.update_one(
                {"_id": town_key},
                {"$set": {"lookup_status": "NOT_FOUND", "provider": "nominatim", "cached_at": time.time()}},
                upsert=True,
            )
            return None
        result = payload[0]
        geometry = result.get("geojson") if isinstance(result, dict) else None
        result_type = str(result.get("type", "")).casefold() if isinstance(result, dict) else ""
        category = str(result.get("category", result.get("class", ""))).casefold() if isinstance(result, dict) else ""
        if (not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon"}
                or result_type not in _ALLOWED_TYPES
                or category not in {"place", "boundary"}):
            self.cache.update_one(
                {"_id": town_key},
                {"$set": {"lookup_status": "NO_TOWN_POLYGON", "provider": "nominatim", "cached_at": time.time()}},
                upsert=True,
            )
            return None
        cached_doc = {
            "_id": town_key,
            "lookup_status": "FOUND",
            "geometry": geometry,
            "display_name": str(result.get("display_name", ""))[:300],
            "provider": "nominatim",
            "cached_at": time.time(),
        }
        self.cache.replace_one({"_id": town_key}, cached_doc, upsert=True)
        return cached_doc

    def check(self, address: str, lat: float, lon: float) -> bool | None:
        town = town_from_address(address)
        if town is None:
            return None
        town_key = town.casefold()
        if town_key not in self._eligible_towns:
            return None
        resolved = self._lookup(town_key)
        if not resolved or not isinstance(resolved.get("geometry"), dict):
            return None
        try:
            return _geometry_contains(resolved["geometry"], lon, lat)
        except (TypeError, ValueError, KeyError, IndexError):
            return None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
