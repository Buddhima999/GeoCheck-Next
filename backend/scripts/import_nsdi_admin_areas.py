"""Download public province/district boundaries from Sri Lanka NSDI.

Run from the repository root with network access:
    python backend/scripts/import_nsdi_admin_areas.py

The output is a local GeoJSON reference file consumed by GeoCheck at startup.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "backend" / "reference-data" / "admin_areas.geojson"
SERVICE = "https://gisapps.nsdi.gov.lk/server/rest/services/Srilanka/Boundaries/MapServer"
LAYERS = (
    (4, "province", "province_name"),
    (3, "district", "district_name"),
)


def main() -> None:
    query = urllib.parse.urlencode(
        {
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        }
    )
    features: list[dict] = []
    for layer_id, level, name_field in LAYERS:
        url = f"{SERVICE}/{layer_id}/query?{query}"
        request = urllib.request.Request(url, headers={"User-Agent": "GeoCheck-Next/0.1"})
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.load(response)
        source_features = payload.get("features", [])
        if not source_features:
            raise RuntimeError(f"NSDI layer {layer_id} returned no features.")
        loaded = 0
        for feature in source_features:
            attributes = feature.get("properties") or {}
            geometry = feature.get("geometry")
            name = str(attributes.get(name_field) or "").strip()
            if not name or not isinstance(geometry, dict):
                continue
            properties = {"level": level, "name": name}
            for field in ("province_name", "district_name", "province_code", "district_code"):
                value = attributes.get(field)
                if value not in (None, ""):
                    properties[field.replace("_name", "").replace("_code", "_code")] = value
            features.append({"type": "Feature", "properties": properties, "geometry": geometry})
            loaded += 1
        print(f"NSDI layer {layer_id} ({level}): imported {loaded} features")

    collection = {"type": "FeatureCollection", "features": features}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".geojson.tmp")
    temporary.write_text(json.dumps(collection, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(OUTPUT)
    print(f"Wrote {len(features)} features to {OUTPUT}")


if __name__ == "__main__":
    main()
