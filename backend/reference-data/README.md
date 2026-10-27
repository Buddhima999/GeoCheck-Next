# Geographic reference datasets

GeoCheck reads versioned reference data from this directory. An absent or ambiguous optional reference leaves its related check `UNVERIFIED`; it must not create a guessed `PASS`.

## Country boundary included in this branch

`country_boundary.geojson` is copied from the existing project repository `Buddhima999/geocheck-vercel`, path `backend/geodata/sri_lanka_boundary.geojson`. The GeoCheck Next loader reads it automatically. It is a starting dataset from the user's earlier project, **not an independently verified cadastral or legal boundary**. Review its source, license, coastline precision, islands and intended use with the provider before using the result as an operational decision. The polygon does not automatically exclude inland lakes or reservoirs.

The reference loader records the file's SHA-256 digest with each validation run so the dataset version can be traced.

## Optional provider reference files

Add these only after confirming they are authoritative and that their license and provider agreement allow use:

- `admin_areas.geojson` — province, district and locality polygons with the documented level/name properties.
- `lea_areas.geojson` — approved geographic exchange/service-area polygons with `phone_prefix` (or `prefix` / `area_code`) and area name properties.
- `provider_regions.csv` — `province,district,region` mapping.

Configure a different directory using `REFERENCE_DATA_DIR` in `backend/.env`. API and worker must use the same versioned directory. Read `docs/REFERENCE_DATA.md` for the accepted schemas.

Never add confidential provider data, personal customer records, or licensed/restricted geography to Git without explicit authorization. Synthetic geometries are for tests only and are never trusted boundaries.
