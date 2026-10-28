# Geographic reference datasets

GeoCheck reads versioned reference data from this directory. An absent or ambiguous optional reference leaves its related check `UNVERIFIED`; it must not create a guessed `PASS`.

## Country boundary included in this branch

`country_boundary.geojson` is copied from the existing project repository `Buddhima999/geocheck-vercel`, path `backend/geodata/sri_lanka_boundary.geojson`. The GeoCheck Next loader reads it automatically. It is a starting dataset from the user's earlier project, **not an independently verified cadastral or legal boundary**. Review its source, license, coastline precision, islands and intended use with the provider before using the result as an operational decision. The polygon does not automatically exclude inland lakes or reservoirs.

The reference loader records the file's SHA-256 digest with each validation run so the dataset version can be traced.
The default `COUNTRY_BOUNDARY_TOLERANCE_M=150` accepts points within 150 m outside the polygon's exterior coastline. Inland polygon holes are not buffered. The configured boundary is checked for customer and DP points.

## Administrative boundaries included in this branch

`admin_areas.geojson` contains the 9 province and 25 district polygon features from the Sri Lanka NSDI Boundaries MapServer. GeoCheck uses these polygons to check a CSV-reported province/district against customer coordinates and derive province/district hierarchy labels. Refresh with `python backend/scripts/import_nsdi_admin_areas.py`. Source: <https://gisapps.nsdi.gov.lk/server/rest/services/Srilanka/Boundaries/MapServer>. Review service terms, attribution and suitability with your organization before operational use. This file does not define telephone exchange boundaries.

## Optional provider reference files

Add these only after confirming they are authoritative and that their license and provider agreement allow use:

- `lea_areas.geojson` — approved geographic exchange/service-area polygons with `phone_prefix` (or `prefix` / `area_code`) and area name properties.
- `lea_area_circles.csv` — optional phone-area center/radius reference with columns `phone_prefix,area_name,center_latitude,center_longitude,radius_m`. Use provider-approved coordinates and radii; GeoCheck does not infer them from town names.
- `provider_regions.csv` — `province,district,region` mapping.

Public OpenStreetMap town lookup is opt-in (`OSM_TOWN_LOOKUP_ENABLED=true`). It extracts the last comma-delimited locality only, selects up to `OSM_TOWN_MAX_LOOKUPS_PER_RUN` most frequent towns (500 by default), requests polygons serially at no more than one per second, and stores results in MongoDB for reuse. Set `OSM_NOMINATIM_EMAIL` to an operator contact. Do not enable this for confidential address data; use an approved/self-hosted service or local `admin_areas.geojson` instead.

Configure a different directory using `REFERENCE_DATA_DIR` in `backend/.env`. API and worker must use the same versioned directory. Read `docs/REFERENCE_DATA.md` for the accepted schemas.

Never add confidential provider data, personal customer records, or licensed/restricted geography to Git without explicit authorization. Synthetic geometries are for tests only and are never trusted boundaries.
