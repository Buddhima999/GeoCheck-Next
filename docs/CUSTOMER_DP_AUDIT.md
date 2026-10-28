# Customer and DP fault audit

## Upload schema

The customer audit upload expects one CSV row per customer with the following mapped columns:

```csv
customer_id,router_number,customer_latitude,customer_longitude,cust_address,reported_province,dp_id,dp_latitude,dp_longitude,line_distance_m
CUST-001,0112345211,6.9272,79.8613,"Example Road, Colombo",Western,DP-001,6.9271,79.8612,120
```

The example values are illustrative only; do not use them as provider data. Headers are matched case-insensitively with punctuation/space normalization and aliases such as `cust_address` → `address`. If your source uses `phone_number` instead of `router_number`, it can be mapped as a legacy alternative.

`router_number` means the customer's fixed-line telephone number, for example `0112345211`; `phone_number` remains accepted as a legacy CSV header. GeoCheck checks the 10-digit Sri Lankan landline format and infers a numbering area from the TRCSL-published prefix list (for example, `011` → Colombo and `081` → Kandy). It compares this inferred area with known locality words in the address, reported district/province and any configured area reference. The check is a consistency warning, not a reverse lookup: a prefix cannot identify the exact house, coordinates, active subscriber, or current location. Exact customer lookup requires an authorized provider subscriber/address system.

## Main rules

- Customer ID, Sri Lankan landline/service number, address, reported province, DP ID, customer coordinates, DP coordinates and line distance are required fields. Missing or malformed values generate findings.
- The reported province is checked against the coordinate point using the bundled NSDI province polygons. The landline area code is cross-checked with the address locality, reported province and coordinate province. For example, `036` indicates the Avissawella numbering area in Western Province; it does not reveal an exact house or exchange-service boundary. The customer phone-area check can only be an exact geometry-based check when provider-approved LEA polygons/circles are installed.
- Both coordinate pairs must be finite geographic coordinates with valid global ranges. The checker flags missing coordinates, invalid values, the `(0,0)` placeholder and possible latitude/longitude swaps.
- The configured Sri Lanka polygon checks valid customer and DP points. `COUNTRY_BOUNDARY_TOLERANCE_M` defaults to 150 m outside exterior edges to account for coastal GPS uncertainty; inland polygon holes are not buffered. A missing polygon returns `UNVERIFIED`, not `PASS`.
- `overhead_distance_m` is generated from customer and DP coordinates using the Haversine formula, following the existing GeoCheck data contract that defines overhead distance as direct geographic separation. `overhead_distance_500m_status` reports whether that direct distance is at most `CUSTOMER_DP_LIMIT_M` (default 500 m).
- The selected `distance_500m_status` defaults to the same direct-distance rule under `DISTANCE_METHOD=straight_line`. `DISTANCE_METHOD=google_routes` asks Google Maps Routes API for a separate driving-road route distance and uses it for the selected route-distance check as well. The road-route value is not the actual aerial overhead cable length; if no route can be returned, the route check is `UNVERIFIED`, and the system never substitutes the straight-line distance in place of a missing route.
- `line_distance_m` is validated as finite, numeric and non-negative. No limit is applied to cable distance by default because a cable path can differ from a straight-line or road-route measurement. Set `MAX_LINE_DISTANCE_M` only if the provider has approved a true business threshold.
- If Google Geocoding is enabled, the returned point is compared to the supplied customer coordinate using `GOOGLE_ADDRESS_MATCH_TOLERANCE_M` (default 500 m). A failed lookup stays `UNVERIFIED`; the system doesn't state that the address is wrong without enough evidence.
- If Google Geocoding is off, address-zone checks require approved locality polygons. The included Sri Lanka country boundary is not a full address or street geocoder.
- Optional OSM town-zone lookup is disabled by default. When deliberately enabled, only the last comma-delimited town name is sent, requests are sequential and limited to at most one per second and the most common towns are cached in MongoDB. Never use public lookup for confidential addresses; prefer provider-approved local polygons or an approved/self-hosted geocoder.
- Optional LEA center/radius checks load `lea_area_circles.csv` with `phone_prefix,area_name,center_latitude,center_longitude,radius_m`. The coordinates and radii must come from an approved provider reference; GeoCheck does not invent them. Mobile numbers remain unverified for area checks and do not fail the customer-location score.
- Dashboard correctness percentages count a row/customer as having no error when no applicable check is `FAIL`; unverified checks are not faults. DP location correctness requires valid consistent coordinates, a boundary `PASS`, and more than the configured fraction of eligible assessable customer assignments passing (default strict majority). DPs without enough evidence remain `UNVERIFIED`.
- Repeated DP IDs with coordinates that conflict beyond the configured tolerance are flagged. Customer ID conflicts across the run are also flagged.
- Each DP is assumed to have 8 customer ports by default (`DP_PORT_CAPACITY=8`). If more than 8 distinct customer IDs in the uploaded CSV are assigned to one DP, the DP is marked `FAIL` with `DP_PORT_CAPACITY_EXCEEDED` and every row assigned to it is flagged. Duplicate source rows with the same normalized customer ID and DP consume one counted port. The DP results table and DP CSV export show assigned customers, capacity, free ports and capacity status. This checks the uploaded assignment data; it does not query a live provider network inventory.

## Status meanings

- `PASS`: the configured rule is satisfied.
- `FAIL`: sufficient evidence shows a fault.
- `UNVERIFIED`: the information/reference is missing, ambiguous, or an external lookup could not be completed.
- `NOT_APPLICABLE`: the rule does not apply to the supplied field or profile.

A single row may have several finding codes, but it counts once as a faulty customer. Dashboard counts distinguish faults from rows requiring verification.

## Reports

From **Customer & DP results**, select a completed run and use:

- **Full report** — all customer rows with original values, calculated distances, per-check statuses and findings.
- **Faults only** — rows whose overall status is `FAIL`.
- **Unverified** — rows whose overall status is `UNVERIFIED`.
- **Export DPs** — DP coordinate-consistency and customer-association summary.

CSV exports preserve source values in the `source_row_values` JSON column as well as dedicated columns for the primary audit fields. Source data is formula-escaped for spreadsheet safety.

## Optional Google Maps setup

1. In Google Cloud, enable Routes API if road-route distance is required, and Geocoding API if address geocoding is required.
2. Configure billing and create a server key restricted to only the APIs and server usage you need. Google documents that Routes API requests use a required field mask; this implementation requests `routes.distanceMeters` only: [Compute Routes documentation](https://developers.google.com/maps/documentation/routes/compute_route_directions).
3. In the root `.env` used by Docker Compose, set `GOOGLE_MAPS_SERVER_KEY`. Keep it on the backend only.
4. Set `DISTANCE_METHOD=google_routes` for the road-route 500 m check. Set `GOOGLE_GEOCODING_ENABLED=true` to enable address geocoding.
5. Keep `GOOGLE_MAPS_MAX_REQUESTS_PER_RUN` bounded to control API usage. The worker caches repeated addresses and coordinate pairs during a run.

The default configuration does not need Google Maps and uses the direct distance. Google usage can incur charges and should be reviewed against the current Google Maps Platform pricing and terms before production use.

## Boundary data warning

`backend/reference-data/country_boundary.geojson` was copied from the user's earlier `Buddhima999/geocheck-vercel` repository. It has not been independently certified against an official legal boundary or reviewed for all islands, coastal detail, inland water, or license terms. Review it with the provider before using boundary results as operational evidence. The app records its SHA-256 digest with each run.
