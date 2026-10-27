# Geographic reference datasets

Place only approved, versioned reference files in this directory. GeoCheck does not ship fabricated boundaries or provider mappings. An absent dataset leaves the related checks UNVERIFIED.

Configure an alternate directory with REFERENCE_DATA_DIR in backend/.env. The API, worker and local project should use the same versioned dataset. Each validation run records the source file SHA-256 digests and inventory that the worker loaded.

Required optional files:

- country_boundary.geojson
- admin_areas.geojson
- lea_areas.geojson
- provider_regions.csv

The parser rejects invalid coordinate bounds and malformed files instead of silently using them. Review the expected schemas and sourcing requirements in docs/REFERENCE_DATA.md before adding real data.

Never commit licensed, confidential or restricted provider geography unless the provider explicitly authorizes storing it in this repository. For local testing, use synthetic geometries in unit tests only; do not treat them as Sri Lankan boundaries.
