"""Optional, versioned geographic references for validation.

No reference geometry or provider-specific mappings are invented here. Files are
loaded only from the configured local reference directory; missing references make
checks return None, which the rules layer records as UNVERIFIED.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import settings
from .validation.rules import haversine_m

logger = logging.getLogger("geocheck.references")
_GRID_DEGREES = 0.25
_PROVINCE_LEVELS = {"province", "provinces"}
_DISTRICT_LEVELS = {"district", "districts"}
_LOCALITY_LEVELS = {"locality", "town", "village", "ds division", "ds_division", "local authority", "local_authority", "city"}
_REQUIRED_REGION_COLUMNS = {"province", "district", "region"}


def _word_tokens(value: object) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", str(value or "").casefold(), flags=re.UNICODE))


def _normalize_label(value: object) -> str:
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def _contains_token_sequence(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(haystack[index:index + len(needle)] == needle for index in range(len(haystack) - len(needle) + 1))


def _clean_phone(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("94") and len(digits) >= 10:
        digits = "0" + digits[2:]
    return digits


def _iter_coordinates(value: Any):
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and isinstance(value[0], (int, float)) and isinstance(value[1], (int, float)):
            yield float(value[0]), float(value[1])
        else:
            for child in value:
                yield from _iter_coordinates(child)


def _point_on_segment(x: float, y: float, a: tuple[float, float], b: tuple[float, float]) -> bool:
    ax, ay = a
    bx, by = b
    cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
    if abs(cross) > 1e-10:
        return False
    return min(ax, bx) - 1e-10 <= x <= max(ax, bx) + 1e-10 and min(ay, by) - 1e-10 <= y <= max(ay, by) + 1e-10


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    if len(ring) < 3:
        return False
    previous = ring[-1]
    for current in ring:
        a = (float(previous[0]), float(previous[1]))
        b = (float(current[0]), float(current[1]))
        if _point_on_segment(lon, lat, a, b):
            return True
        x1, y1 = a
        x2, y2 = b
        if (y1 > lat) != (y2 > lat):
            intersection_x = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < intersection_x:
                inside = not inside
        previous = current
    return inside


def _point_in_polygon(lon: float, lat: float, polygon: list[Any]) -> bool:
    if not polygon or not _point_in_ring(lon, lat, polygon[0]):
        return False
    # A hole excludes the point, unless it lies on the hole boundary. Boundary
    # classification is treated conservatively as inside the feature for this MVP.
    for hole in polygon[1:]:
        if _point_in_ring(lon, lat, hole):
            if any(
                _point_on_segment(lon, lat, (float(a[0]), float(a[1])), (float(b[0]), float(b[1])))
                for a, b in zip(hole, hole[1:] + hole[:1])
            ):
                return True
            return False
    return True


def _geometry_contains(geometry: dict[str, Any], lon: float, lat: float) -> bool:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        return _point_in_polygon(lon, lat, coordinates)
    if geometry_type == "MultiPolygon":
        return any(_point_in_polygon(lon, lat, polygon) for polygon in coordinates or [])
    return False


def _exterior_segments(geometry: dict[str, Any]):
    """Yield only exterior polygon edges; coastal tolerance must not fill inland holes."""
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    polygons = [coordinates] if geometry_type == "Polygon" else coordinates if geometry_type == "MultiPolygon" else []
    for polygon in polygons:
        if not polygon:
            continue
        ring = polygon[0]
        for start, end in zip(ring, ring[1:] + ring[:1]):
            yield start, end


def _distance_to_segment_m(lon: float, lat: float, start: list[float], end: list[float]) -> float:
    """Approximate local point-to-edge distance, accurate at the short coastal buffer scale."""
    mean_lat = math.radians(lat)
    x_scale = 111_320.0 * math.cos(mean_lat)
    y_scale = 111_320.0
    px, py = lon * x_scale, lat * y_scale
    ax, ay = float(start[0]) * x_scale, float(start[1]) * y_scale
    bx, by = float(end[0]) * x_scale, float(end[1]) * y_scale
    dx, dy = bx - ax, by - ay
    denominator = dx * dx + dy * dy
    t = 0.0 if denominator == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denominator))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


@dataclass(frozen=True)
class SpatialFeature:
    properties: dict[str, Any]
    geometry: dict[str, Any]
    bounds: tuple[float, float, float, float]

    def contains(self, lon: float, lat: float) -> bool:
        min_lon, min_lat, max_lon, max_lat = self.bounds
        if not (min_lon - 1e-10 <= lon <= max_lon + 1e-10 and min_lat - 1e-10 <= lat <= max_lat + 1e-10):
            return False
        return _geometry_contains(self.geometry, lon, lat)


class SpatialFeatureIndex:
    """A small grid index that avoids testing every polygon for every customer row."""

    def __init__(self, features: list[SpatialFeature], *, grid_degrees: float = _GRID_DEGREES):
        self.features = features
        self.grid_degrees = grid_degrees
        self.buckets: dict[tuple[int, int], list[int]] = {}
        for index, feature in enumerate(features):
            min_lon, min_lat, max_lon, max_lat = feature.bounds
            x_start, x_end = math.floor(min_lon / grid_degrees), math.floor(max_lon / grid_degrees)
            y_start, y_end = math.floor(min_lat / grid_degrees), math.floor(max_lat / grid_degrees)
            # Reject pathological source geometries instead of making a huge index.
            cells = (x_end - x_start + 1) * (y_end - y_start + 1)
            if cells > 100_000:
                logger.warning("Skipping spatial-index entry with an unreasonable bounding box.")
                continue
            for x_cell in range(x_start, x_end + 1):
                for y_cell in range(y_start, y_end + 1):
                    self.buckets.setdefault((x_cell, y_cell), []).append(index)

    def candidates(self, lon: float, lat: float) -> list[SpatialFeature]:
        indexes = self.buckets.get(
            (math.floor(lon / self.grid_degrees), math.floor(lat / self.grid_degrees)),
            [],
        )
        return [self.features[index] for index in indexes]

    def containing(self, lon: float, lat: float) -> list[SpatialFeature]:
        return [feature for feature in self.candidates(lon, lat) if feature.contains(lon, lat)]


def _read_geojson(path: Path) -> tuple[list[SpatialFeature], str | None]:
    if not path.is_file():
        return [], None
    digest = hashlib.sha256()
    with path.open("rb") as binary:
        for chunk in iter(lambda: binary.read(1024 * 1024), b""):
            digest.update(chunk)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not parse reference file {path.name}: {exc}") from exc
    features: list[SpatialFeature] = []
    for feature in payload.get("features", []) if isinstance(payload, dict) else []:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        properties = feature.get("properties") or {}
        if not isinstance(geometry, dict) or not isinstance(properties, dict):
            continue
        if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            continue
        coordinates = list(_iter_coordinates(geometry.get("coordinates")))
        if not coordinates:
            continue
        min_lon = min(point[0] for point in coordinates)
        max_lon = max(point[0] for point in coordinates)
        min_lat = min(point[1] for point in coordinates)
        max_lat = max(point[1] for point in coordinates)
        if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180 and -90 <= min_lat <= 90 and -90 <= max_lat <= 90):
            raise ValueError(f"Reference geometry in {path.name} contains out-of-range coordinates.")
        features.append(
            SpatialFeature(
                properties=dict(properties),
                geometry=geometry,
                bounds=(min_lon, min_lat, max_lon, max_lat),
            )
        )
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ValueError(f"{path.name} must be a GeoJSON FeatureCollection.")
    return features, digest.hexdigest()


def _prop(feature: SpatialFeature, *names: str) -> str:
    for name in names:
        value = feature.properties.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _level(feature: SpatialFeature) -> str:
    return re.sub(r"[^a-z]+", "_", _prop(feature, "level", "admin_level", "area_level", "kind").casefold()).strip("_")


def _inside_named_area(
    index: SpatialFeatureIndex,
    lon: float,
    lat: float,
    *,
    levels: set[str],
) -> list[SpatialFeature]:
    matches = []
    for feature in index.containing(lon, lat):
        if _level(feature) in levels:
            matches.append(feature)
    return matches


def _read_provider_regions(path: Path) -> tuple[dict[tuple[str, str], set[str]], str | None]:
    if not path.is_file():
        return {}, None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream, strict=True)
            headers = {str(header or "").strip().casefold() for header in (reader.fieldnames or [])}
            if not _REQUIRED_REGION_COLUMNS.issubset(headers):
                raise ValueError("provider_regions.csv must have province,district,region columns.")
            result: dict[tuple[str, str], set[str]] = {}
            for line_number, row in enumerate(reader, start=2):
                province = _normalize_label(row.get("province", ""))
                district = _normalize_label(row.get("district", ""))
                region = _normalize_label(row.get("region", ""))
                if not province or not district or not region:
                    raise ValueError(f"provider_regions.csv row {line_number} has an empty required value.")
                result.setdefault((province, district), set()).add(region)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ValueError(f"Could not read provider_regions.csv: {exc}") from exc
    return result, digest


def _read_lea_circles(path: Path) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    """Load provider-approved phone-prefix circles without inventing centers."""
    if not path.is_file():
        return {}, None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    required = {"phone_prefix", "area_name", "center_latitude", "center_longitude", "radius_m"}
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream, strict=True)
            headers = {str(header or "").strip().casefold() for header in (reader.fieldnames or [])}
            if not required.issubset(headers):
                raise ValueError("lea_area_circles.csv must have phone_prefix,area_name,center_latitude,center_longitude,radius_m columns.")
            circles: dict[str, list[dict[str, Any]]] = {}
            for line_number, row in enumerate(reader, start=2):
                prefix = _clean_phone(row.get("phone_prefix", ""))
                area_name = str(row.get("area_name", "")).strip()
                try:
                    lat = float(row.get("center_latitude", ""))
                    lon = float(row.get("center_longitude", ""))
                    radius = float(row.get("radius_m", ""))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"lea_area_circles.csv row {line_number} has invalid center/radius values.") from exc
                if (not prefix or not area_name or not all(map(math.isfinite, (lat, lon, radius)))
                        or not 0 <= lat <= 90 or not -180 <= lon <= 180 or radius <= 0):
                    raise ValueError(f"lea_area_circles.csv row {line_number} has an invalid prefix, center, or radius.")
                circles.setdefault(prefix, []).append({
                    "area_name": area_name, "latitude": lat, "longitude": lon, "radius_m": radius,
                })
            return circles, digest
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ValueError(f"Could not read lea_area_circles.csv: {exc}") from exc


class ReferenceData:
    def __init__(
        self,
        *,
        root: Path,
        country_features: list[SpatialFeature],
        admin_features: list[SpatialFeature],
        lea_features: list[SpatialFeature],
        provider_regions: dict[tuple[str, str], set[str]],
        lea_circles: dict[str, list[dict[str, Any]]] | None = None,
        versions: dict[str, str | None],
        country_boundary_tolerance_m: float = 150.0,
    ):
        self.root = root
        self.country_index = SpatialFeatureIndex(country_features)
        self.admin_index = SpatialFeatureIndex(admin_features)
        self.lea_index = SpatialFeatureIndex(lea_features)
        self.provider_regions = provider_regions
        self.versions = versions
        self.country_boundary_tolerance_m = max(0.0, country_boundary_tolerance_m)

        self.has_province_features = any(_level(feature) in _PROVINCE_LEVELS for feature in admin_features)
        self.has_district_features = any(_level(feature) in _DISTRICT_LEVELS for feature in admin_features)
        self.locality_by_token: dict[str, list[tuple[tuple[str, ...], SpatialFeature]]] = {}
        generic_tokens = {
            "road", "rd", "street", "st", "lane", "ln", "avenue", "ave",
            "town", "city", "village", "division", "locality", "area",
            "north", "south", "east", "west", "central",
        }
        for feature in admin_features:
            if _level(feature) not in _LOCALITY_LEVELS:
                continue
            raw_names = [_prop(feature, "name", "locality", "town", "village", "ds_division")]
            aliases = feature.properties.get("aliases", [])
            if isinstance(aliases, list):
                raw_names.extend(str(alias) for alias in aliases)
            for raw_name in raw_names:
                tokens = _word_tokens(raw_name)
                if len(_normalize_label(raw_name)) < 3 or not tokens:
                    continue
                searchable = [token for token in tokens if token not in generic_tokens] or list(tokens)
                for token in set(searchable):
                    self.locality_by_token.setdefault(token, []).append((tokens, feature))

        self.lea_by_prefix: dict[str, list[SpatialFeature]] = {}
        self.max_phone_prefix_length = 0
        for feature in lea_features:
            prefix = _clean_phone(_prop(feature, "phone_prefix", "prefix", "area_code"))
            if prefix:
                self.lea_by_prefix.setdefault(prefix, []).append(feature)
                self.max_phone_prefix_length = max(self.max_phone_prefix_length, len(prefix))
        self.lea_circles = lea_circles or {}
        self.max_phone_prefix_length = max(
            self.max_phone_prefix_length,
            max((len(prefix) for prefix in self.lea_circles), default=0),
        )

    @classmethod
    def load(cls, root: Path | None = None, *, country_boundary_tolerance_m: float | None = None) -> "ReferenceData":
        base = (root or settings.resolved_reference_data_dir).resolve()
        country, country_hash = _read_geojson(base / "country_boundary.geojson")
        admin, admin_hash = _read_geojson(base / "admin_areas.geojson")
        lea, lea_hash = _read_geojson(base / "lea_areas.geojson")
        regions, regions_hash = _read_provider_regions(base / "provider_regions.csv")
        lea_circles, lea_circles_hash = _read_lea_circles(base / "lea_area_circles.csv")
        return cls(
            root=base,
            country_features=country,
            admin_features=admin,
            lea_features=lea,
            provider_regions=regions,
            lea_circles=lea_circles,
            versions={
                "country_boundary.geojson": country_hash,
                "admin_areas.geojson": admin_hash,
                "lea_areas.geojson": lea_hash,
                "lea_area_circles.csv": lea_circles_hash,
                "provider_regions.csv": regions_hash,
            },
            country_boundary_tolerance_m=(
                settings.country_boundary_tolerance_m
                if country_boundary_tolerance_m is None else country_boundary_tolerance_m
            ),
        )

    def status(self) -> dict[str, Any]:
        def item(filename: str, count: int) -> dict[str, Any]:
            version = self.versions.get(filename)
            return {
                "configured": version is not None and count > 0,
                "feature_or_mapping_count": count,
                "sha256": version,
                "status": "LOADED" if version is not None and count > 0 else "MISSING",
            }

        return {
            "directory_configured": self.root.is_dir(),
            "country_boundary_tolerance_m": self.country_boundary_tolerance_m,
            "country_boundary": item("country_boundary.geojson", len(self.country_index.features)),
            "administrative_areas": item("admin_areas.geojson", len(self.admin_index.features)),
            "lea_areas": item("lea_areas.geojson", len(self.lea_index.features)),
            "lea_area_circles": item("lea_area_circles.csv", sum(len(v) for v in self.lea_circles.values())),
            "provider_regions": item("provider_regions.csv", len(self.provider_regions)),
            "warning": "Only approved, versioned reference data should be placed in this directory. Missing files do not create PASS results.",
        }

    def country_check(self, lat: float, lon: float) -> bool | None:
        if not self.country_index.features:
            return None
        candidates = self.country_index.candidates(lon, lat)
        if any(feature.contains(lon, lat) for feature in candidates):
            return True
        if self.country_boundary_tolerance_m <= 0:
            return False
        margin = self.country_boundary_tolerance_m / 109_000.0
        near_features = [
            feature for feature in self.country_index.features
            if feature.bounds[0] - margin <= lon <= feature.bounds[2] + margin
            and feature.bounds[1] - margin <= lat <= feature.bounds[3] + margin
        ]
        if not near_features:
            return False
        nearest = min(
            (
                _distance_to_segment_m(lon, lat, start, end)
                for feature in near_features
                for start, end in _exterior_segments(feature.geometry)
            ),
            default=math.inf,
        )
        return nearest <= self.country_boundary_tolerance_m

    def address_zone_check(self, address: str, lat: float, lon: float) -> bool | None:
        if not self.locality_by_token:
            return None
        address_tokens = _word_tokens(address)
        if not address_tokens:
            return None
        candidate_names: dict[tuple[tuple[str, ...], int], SpatialFeature] = {}
        for token in set(address_tokens):
            for name_tokens, feature in self.locality_by_token.get(token, []):
                if _contains_token_sequence(address_tokens, name_tokens):
                    candidate_names[(name_tokens, id(feature))] = feature
        if not candidate_names:
            return None
        return any(feature.contains(lon, lat) for feature in candidate_names.values())

    def phone_area_check(self, phone: str, lat: float, lon: float) -> bool | None:
        normalized_phone = _clean_phone(phone)
        if not normalized_phone:
            return None
        best: list[SpatialFeature] = []
        max_length = min(len(normalized_phone), self.max_phone_prefix_length)
        for length in range(1, max_length + 1):
            match = self.lea_by_prefix.get(normalized_phone[:length])
            if match:
                best = match
        if not best:
            prefix = next(
                (normalized_phone[:length] for length in range(max_length, 0, -1) if normalized_phone[:length] in self.lea_circles),
                None,
            )
            if prefix is None:
                return None
            return any(
                haversine_m(lat, lon, circle["latitude"], circle["longitude"]) <= circle["radius_m"]
                for circle in self.lea_circles[prefix]
            )
        identities = {
            (
                _normalize_label(_prop(feature, "name", "lea_name", "area_name")),
                feature.bounds,
            )
            for feature in best
        }
        if len(identities) > 1:
            return None
        return any(feature.contains(lon, lat) for feature in best)

    def geography_check(self, values: dict[str, Any], lat: float, lon: float) -> bool | None:
        reported_province = str(values.get("reported_province", "") or "").strip()
        reported_district = str(values.get("reported_district", "") or "").strip()
        reported_region = str(values.get("reported_region", "") or "").strip()
        observed = False
        province_features = _inside_named_area(self.admin_index, lon, lat, levels=_PROVINCE_LEVELS)
        district_features = _inside_named_area(self.admin_index, lon, lat, levels=_DISTRICT_LEVELS)

        if reported_province:
            if not self.has_province_features:
                return None
            observed = True
            expected = {_normalize_label(_prop(feature, "name", "province", "province_name")) for feature in province_features}
            if not expected or _normalize_label(reported_province) not in expected:
                return False

        if reported_district:
            if not self.has_district_features:
                return None
            observed = True
            expected = {_normalize_label(_prop(feature, "name", "district", "district_name")) for feature in district_features}
            if not expected or _normalize_label(reported_district) not in expected:
                return False

        if reported_region:
            if not self.provider_regions:
                return None
            observed = True
            if not province_features or not district_features:
                return None
            province_names = {_normalize_label(_prop(feature, "name", "province", "province_name")) for feature in province_features}
            district_names = {_normalize_label(_prop(feature, "name", "district", "district_name")) for feature in district_features}
            mappings: set[str] = set()
            for province_name in province_names:
                for district_name in district_names:
                    pair_regions = self.provider_regions.get((province_name, district_name))
                    if pair_regions is None:
                        continue
                    # An official province/district pair mapped to more than one
                    # provider region is ambiguous. Never select one arbitrarily.
                    if len(pair_regions) != 1:
                        return None
                    mappings.update(pair_regions)
            if len(mappings) != 1:
                return None
            if _normalize_label(reported_region) not in mappings:
                return False

        return True if observed else None

    def resolve_geography(self, lat: float, lon: float) -> dict[str, str | None]:
        """Resolve coordinate labels from configured province/district/region references."""
        province_features = _inside_named_area(self.admin_index, lon, lat, levels=_PROVINCE_LEVELS)
        district_features = _inside_named_area(self.admin_index, lon, lat, levels=_DISTRICT_LEVELS)

        def unique_name(features: list[SpatialFeature], *keys: str) -> str | None:
            names = {
                _normalize_label(_prop(feature, *keys)): str(_prop(feature, *keys)).strip()
                for feature in features
                if _normalize_label(_prop(feature, *keys))
            }
            return next(iter(names.values())) if len(names) == 1 else None

        province = unique_name(province_features, "name", "province", "province_name")
        district = unique_name(district_features, "name", "district", "district_name")
        region: str | None = None
        if province and district:
            region_names = self.provider_regions.get(
                (_normalize_label(province), _normalize_label(district)), set()
            )
            if len(region_names) == 1:
                region = next(iter(region_names)).title()
        return {"province": province, "district": district, "region": region}

    def callbacks(self) -> dict[str, Callable[..., bool | None]]:
        return {
            "country_check": self.country_check,
            "phone_area_check": self.phone_area_check,
            "address_zone_check": self.address_zone_check,
            "geography_check": self.geography_check,
            "geography_resolver": self.resolve_geography,
        }


def load_reference_data() -> ReferenceData:
    """Load configured references and log only their inventory, never customer data."""
    references = ReferenceData.load()
    logger.info("Reference inventory: %s", {
        key: value["status"]
        for key, value in references.status().items()
        if isinstance(value, dict)
    })
    return references
