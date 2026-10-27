# Trusted geographic reference data

GeoCheck intentionally does not invent official areas. The checks use local, approved files and report UNVERIFIED when needed reference data is missing, ambiguous or not configured.

## Supported files

Put these files in backend/reference-data by default, or set REFERENCE_DATA_DIR to a shared reference directory.

### country_boundary.geojson

A GeoJSON FeatureCollection containing Polygon or MultiPolygon features defining the provider-approved accepted boundary. Coordinate positions follow GeoJSON order: longitude, latitude. This is used by the country-boundary check.

Use a source that the provider accepts for subscriber-location validation. Record source, license, version/date and checksum outside the application as part of your operational data governance. The project does not supply an official Sri Lankan polygon.

### admin_areas.geojson

A GeoJSON FeatureCollection containing administrative/locality polygons. Every feature needs a properties.level and properties.name value. Supported values for level:
- province
- district
- locality, town, village, ds_division, local_authority, or city for address-zone checks

Optional aliases can be provided as a string array in properties.aliases for locality/address name matching. Province and district features are evaluated by their level and name. For polygons with multiple names, use the name the provider recognizes and include approved aliases.

This file is not a geocoder. The address-zone checker only validates a locality when a locality name or approved alias is found in the submitted address. If no name can be matched, the result remains UNVERIFIED instead of guessing the town from free text.

### lea_areas.geojson

A GeoJSON FeatureCollection whose features represent approved telephone exchange/local exchange area polygons. Each feature must have a phone_prefix property (prefix and area_code are also accepted), plus Polygon/MultiPolygon geometry. The longest matching number prefix is used. A missing prefix or absent/ambiguous reference makes the check UNVERIFIED.

Country-code normalization handles +94 / 94 prefixes for Sri Lankan numbers. Mobile numbers beginning with 07 are not used to infer town-level location.

### provider_regions.csv

UTF-8 CSV with exactly these required headers (additional headers are ignored):

~~~csv
province,district,region
Official Province Label,Official District Label,Official Provider Region Label
~~~

Rows are normalized for case and punctuation, but labels should match the provider's authoritative list. If a province/district pair has multiple distinct provider regions, the mapping is treated as ambiguous rather than arbitrarily selecting one.

## Check the loaded inventory

Set GEOCHECK_API_KEY in backend/.env, start MongoDB and the API, then request:

~~~powershell
curl.exe -H "X-API-Key: YOUR_SECRET" http://127.0.0.1:8000/api/reference-data/status
~~~

The response shows file status, parsed feature/mapping counts, and SHA-256 digests. Every validation run records the digests and the inventory observed by its worker. If files change while the worker is running, restart the worker so a run cannot silently switch reference versions mid-run.

## Quality and legal controls before real use

1. Obtain provider approval for the source, projection/geometry quality, temporal version and license.
2. Validate geometry coverage, overlaps, holes, invalid rings, and boundary points with GIS tooling before release. The built-in point-in-polygon helper is a pragmatic local check, not a full GIS topology validator.
3. For production, maintain versioned immutable releases and deploy atomically; do not edit files in place while imports run.
4. Document reference update approval, who changed it, effective date, and rollback procedure.
5. Run a reviewed set of known-inside, known-outside, on-boundary and ambiguous test points.

The repository's reference-data unit tests use small synthetic rectangles to verify software behavior only. They are not real location data and must not be used for Sri Lankan customer validation.
