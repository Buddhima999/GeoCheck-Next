# GeoCheck Next — Validation Rules

## Status semantics

- `PASS`: sufficient evidence supports the rule.
- `FAIL`: sufficient evidence demonstrates a violation.
- `UNVERIFIED`: insufficient or ambiguous evidence.
- `NOT_APPLICABLE`: rule does not apply to this record.

Missing results must never default to PASS.

## Required customer audit CSV

The upload profile requires these mapped fields: `customer_id`, `router_number`, `customer_latitude`, `customer_longitude`, `cust_address` (mapped to logical `address`), `dp_id`, `dp_latitude`, `dp_longitude`, and `line_distance_m`. Header matching ignores punctuation/case and accepts common aliases. The legacy `phone_number` field can substitute for `router_number` when present. Missing required columns are rejected during upload; invalid row values become findings in the report.

`router_number` is the customer's Sri Lankan fixed-line telephone number (legacy `phone_number` is accepted as an alternative). The validator checks the number format, parses domestic `0XX XXX XXXX` and international `+94 XX XXX XXXX` forms, and uses the TRCSL-published geographic area-code list to infer a numbering area. For example, `0112345211` maps to the Colombo numbering area. The inferred area is compared with locality text in `address` and the supplied district/province, and with an optional configured coordinate-to-area reference. A mismatch is a review finding; incomplete evidence remains `UNVERIFIED`. Area codes do not reveal exact house coordinates, prove subscriber ownership, or establish the customer's current location. See the [TRCSL numbering resources](https://www.trc.gov.lk/pages_e.php?id=121) and [published numbering manual](https://www.trc.gov.lk/content/files/policy/numberingmanual.pdf).

## Proposed rule catalogue

| Code | Entity | Rule | Notes |
|---|---|---|---|
| `COORD_MISSING` | Customer / DP observation | Required coordinates missing | Skip spatial checks |
| `COORD_NOT_NUMERIC` | Customer / DP observation | Cannot parse coordinate | Fail |
| `COORD_OUT_OF_RANGE` | Customer / DP observation | Latitude outside [-90, 90] or longitude outside [-180, 180] | Fail |
| `COORD_ZERO_PLACEHOLDER` | Customer / DP observation | Both coordinates zero | Flag placeholder |
| `COORD_POSSIBLE_SWAP` | Customer / DP observation | Swapped values make geographic sense while originals do not | Warning; never auto-correct |
| `COUNTRY_BOUNDARY` | Customer | Customer point outside the configured Sri Lanka polygon | Boundary data is a starting asset from the legacy repository; verify source/accuracy before operational use |
| `PHONE_AREA_MISMATCH` | Customer | Geographic phone area conflicts with location-derived area | Apply approved LEA table; normally a review finding |
| `ADDRESS_ZONE_MISMATCH` | Customer | Coordinates outside reliably resolved locality zone | Unverified if locality ambiguous or zone unavailable |
| `CUSTOMER_DP_DISTANCE` | Customer-DP relationship | Straight-line distance exceeds threshold | Association anomaly; use only eligible customer evidence for DP scoring |
| `OVERHEAD_DISTANCE_MISMATCH` | Distance field | A supplied overhead-distance value differs beyond approved tolerance from coordinates | Applied when the source CSV includes a reported overhead distance; the 25 m tolerance remains provisional pending approval |
| `LINE_DISTANCE_INVALID` | Distance field | Non-numeric, negative or invalid value | Invalid line distance must never pass merely because it is below threshold |
| `DP_COORD_CONFLICT` | DP | Same normalized DP ID has materially different coordinate observations | Report groups/rows; never choose a winner automatically |
| `CUSTOMER_ID_CONFLICT` | Source row/customer | Same normalized customer ID has incompatible source records | Preserve each row |

## Distance semantics

Two distance measurements are kept separate:

- The output `overhead_distance_m` is calculated from customer and DP coordinates using Haversine. `overhead_distance_500m_status` reports whether that direct distance is within `CUSTOMER_DP_LIMIT_M` (default 500 m), separately from any road-route measurement.
- `DISTANCE_METHOD=straight_line` (default) uses Haversine for the main 500 m relationship check. It is local and needs no Google API key.
- `DISTANCE_METHOD=google_routes` calls the Google Maps Routes API in `DRIVE` mode and compares its road-route distance with `CUSTOMER_DP_LIMIT_M` (default 500 m). Google requires a response field mask; the implementation requests only `routes.distanceMeters`. The route value is not the overhead cable length. Without a key, with an unavailable route, or after the per-run API request cap, the route check remains `UNVERIFIED` and never falls back silently to Haversine. See the [Routes API](https://developers.google.com/maps/documentation/routes/compute_route_directions) documentation.
- The report keeps the Haversine direct distance separate from any Google road-route distance. It does not claim that either one is actual cable length.
- `line_distance_m` is checked for presence, finiteness and non-negative numeric value. No maximum is assumed by default because actual cable length can differ from direct or road distance. A provider-approved maximum can be configured with `MAX_LINE_DISTANCE_M`.
- If the input CSV separately includes a source `overhead_distance_m`, it is preserved in the source values and exported as `reported_overhead_distance_m`; it is compared with the coordinate-derived value using the configured tolerance (25 m by default) because the existing project contract defines this measurement as direct customer-to-DP separation.

### Google address validation

When `GOOGLE_GEOCODING_ENABLED=true` and a backend-only `GOOGLE_MAPS_SERVER_KEY` is configured, each distinct address is geocoded with a per-run request cap and cache. The result is compared to customer coordinates using `GOOGLE_ADDRESS_MATCH_TOLERANCE_M` (default 500 m). If geocoding fails, the system reports `UNVERIFIED`; it does not assert that the customer address is wrong. When Google geocoding is off, approved locality polygons may provide an area-level check; absent reference data leaves it unverified. Google Maps API access requires the appropriate APIs and billing configuration; restrict the key to the required APIs and backend usage.

## Geographic boundary

The bundled boundary is copied from the legacy project and is not independently certified. The point-in-polygon check determines whether a coordinate falls inside that configured polygon; it does not independently prove an address, active service, or cadastral land status. Boundary precision, island coverage and inland-water handling must be reviewed with the provider before operational decisions.

## Aggregation

- A row with several findings counts once toward rows-with-errors; each finding remains individually queryable.
- Customer and DP metrics report passing, failing and unverified counts.
- DP physical-location quality and DP-customer association quality are separate.
- Customers with confirmed location errors are ineligible to count as evidence against DP association quality.
- Customer IDs are checked across the completed source run after batch processing. Rows sharing an ID are marked `CUSTOMER_ID_CONFLICT` when rounded coordinates (5 decimal places), trimmed/case-normalized address, or phone digits differ; identical repeated signatures are preserved without this conflict finding.
- Provider-region checks stay `UNVERIFIED` when the authoritative mapping is missing or ambiguous. The app does not invent a winning region.
- Define minimum eligible DP customers and required pass percentage before implementing final aggregate DP pass/fail.
- Dashboard totals must reconcile with source rows, distinct entities and findings.
