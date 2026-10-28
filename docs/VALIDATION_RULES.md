# GeoCheck Next — Validation Rules

## Status semantics

- `PASS`: sufficient evidence supports the rule.
- `FAIL`: sufficient evidence demonstrates a violation.
- `UNVERIFIED`: insufficient or ambiguous evidence.
- `NOT_APPLICABLE`: rule does not apply to this record.

Missing results must never default to PASS.

## Required customer audit CSV

The upload profile requires these mapped fields: `customer_id`, `router_number`, `customer_latitude`, `customer_longitude`, `cust_address` (mapped to logical `address`), `reported_province`, `dp_id`, `dp_latitude`, `dp_longitude`, and `line_distance_m`. Header matching ignores punctuation/case and accepts common aliases. The legacy `phone_number` field can substitute for `router_number` when present. Missing required columns are rejected during upload; invalid row values become findings in the report.

`router_number` is the customer's Sri Lankan fixed-line telephone number (legacy `phone_number` is accepted as an alternative). The validator checks the number format, parses domestic `0XX XXX XXXX` and international `+94 XX XXX XXXX` forms, and uses the TRCSL-published geographic area-code list to infer a numbering area. For example, `0112345211` maps to the Colombo numbering area. The inferred area is compared with locality text in `address`, the supplied province/district, and the customer coordinate?s province using the bundled Sri Lanka NSDI province and district polygons. Provider-approved exchange polygons or circles can add exact area checks when supplied. For example, `036` indicates Avissawella in Western Province; the province/numbering-area match is a plausibility check, not proof of the exact house or telephone exchange coverage. Mobile numbers are `UNVERIFIED` for area validation and are not treated as location errors. Area codes do not reveal exact house coordinates, prove subscriber ownership, or establish the customer's current location. See the [TRCSL numbering resources](https://www.trc.gov.lk/pages_e.php?id=121) and [published numbering manual](https://www.trc.gov.lk/content/files/policy/numberingmanual.pdf).

## Proposed rule catalogue

| Code | Entity | Rule | Notes |
|---|---|---|---|
| `COORD_MISSING` | Customer / DP observation | Required coordinates missing | Skip spatial checks |
| `COORD_NOT_NUMERIC` | Customer / DP observation | Cannot parse coordinate | Fail |
| `COORD_OUT_OF_RANGE` | Customer / DP observation | Latitude outside [-90, 90] or longitude outside [-180, 180] | Fail |
| `COORD_ZERO_PLACEHOLDER` | Customer / DP observation | Both coordinates zero | Flag placeholder |
| `COORD_POSSIBLE_SWAP` | Customer / DP observation | Swapped values make geographic sense while originals do not | Warning; never auto-correct |
| `COUNTRY_BOUNDARY` / `DP_COUNTRY_BOUNDARY` | Customer / DP | Point is outside the configured Sri Lanka polygon and its coastal tolerance | `COUNTRY_BOUNDARY_TOLERANCE_M` defaults to 150 m; boundary data remains unverified source material |
| `ROUTER_NUMBER_FORMAT_INVALID` | Customer | Non-mobile number is not a 10-digit Sri Lankan fixed-line number | Mobile number area is `UNVERIFIED`, not a fault |
| `ROUTER_NUMBER_AREA_CODE_UNVERIFIED` | Customer | Ten-digit number prefix is not in the configured geographic landline list | Leave unverified rather than guessing a locality |
| `PHONE_AREA_MISMATCH` | Customer | Geographic phone area conflicts with address, reported geography or configured coordinate-area reference | Review finding; number area is not exact geolocation |
| `PHONE_AREA_UNVERIFIED` | Customer | Numbering area is known, but independent location evidence is insufficient | Needs address locality or approved area geometry |
| `ADDRESS_ZONE_MISMATCH` | Customer | Coordinates outside reliably resolved locality zone | Unverified if locality ambiguous or zone unavailable |
| `CUSTOMER_DP_DISTANCE` | Customer-DP relationship | Straight-line distance exceeds threshold | Association anomaly; use only eligible customer evidence for DP scoring |
| `OVERHEAD_DISTANCE_MISMATCH` | Distance field | A supplied overhead-distance value differs beyond approved tolerance from coordinates | Applied when the source CSV includes a reported overhead distance; the 25 m tolerance remains provisional pending approval |
| `LINE_DISTANCE_INVALID` | Distance field | Non-numeric, negative or invalid value | Invalid line distance must never pass merely because it is below threshold |
| `DP_COORD_CONFLICT` | DP | Same normalized DP ID has materially different coordinate observations | Report groups/rows; never choose a winner automatically |
| `DP_PHONE_AREA_MISMATCH` | DP / assignment | DP lies outside the configured area for an assigned customer's fixed-line prefix | Requires approved LEA polygon or center/radius reference |
| `DP_PORT_CAPACITY_EXCEEDED` | DP / customer-DP assignment | More distinct customer IDs assigned to a DP than configured ports | Defaults to 8 ports; duplicate rows for one normalized customer at the same DP count once |
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

When `GOOGLE_GEOCODING_ENABLED=true` and a backend-only `GOOGLE_MAPS_SERVER_KEY` is configured, each distinct address is geocoded with a per-run request cap and cache. The result is compared to customer coordinates using `GOOGLE_ADDRESS_MATCH_TOLERANCE_M` (default 500 m). Alternatively, `OSM_TOWN_LOOKUP_ENABLED=true` uses only the last comma-delimited town token, selects the most common towns up to the configured cap, requests boundaries sequentially at <=1 request/second, and caches polygons in MongoDB. It requires an operator contact email and remains off by default. Do not send confidential address data to public Nominatim. If a town polygon cannot be resolved, the result is `UNVERIFIED`. When external geocoding is off, approved locality polygons may provide the area-level check; absent reference data leaves it unverified.

## Geographic boundary

The bundled boundary is copied from the legacy project and is not independently certified. Both customer and DP points use the configured polygon, with `COUNTRY_BOUNDARY_TOLERANCE_M` (150 m by default) applied only outside exterior polygon edges; inland holes are not buffered. This does not independently prove an address, active service, or cadastral land status. Boundary precision, island coverage and inland-water handling must be reviewed with the provider before operational decisions.

## Aggregation

- A row with several findings counts once toward rows-with-errors; each finding remains individually queryable.
- Customer and DP metrics report passing, failing and unverified counts.
- The dashboard's CSV-row and customer-location percentages count records without a confirmed `FAIL`; `UNVERIFIED` checks do not count as errors. The DP-location percentage counts only DPs with `PASS`.
- A DP is `PASS` only when its coordinate observations are consistent, it passes the country-boundary check, and a strict majority of eligible, assessable customer assignments pass distance and available phone-area evidence. The default majority threshold is configured by `DP_ASSOCIATION_MIN_PASS_FRACTION=0.5`; a DP with no assessable customer evidence is `UNVERIFIED`.
- Customers with confirmed location errors are ineligible to count as evidence against DP association quality.
- Customer IDs are checked across the completed source run after batch processing. Rows sharing an ID are marked `CUSTOMER_ID_CONFLICT` when rounded coordinates (5 decimal places), trimmed/case-normalized address, or phone digits differ; identical repeated signatures are preserved without this conflict finding.
- DP port usage is counted per uploaded run from distinct normalized customer IDs assigned to each normalized DP ID. `DP_PORT_CAPACITY` defaults to 8; a DP with more than that number receives `DP_PORT_CAPACITY_EXCEEDED`, and all customer rows assigned to the overloaded DP are flagged. Duplicate rows with the same customer ID on the same DP use one port. Rows without a customer ID are counted separately so missing IDs cannot silently lower the count. This is a CSV consistency check, not a live network or physical port-state query.
- Provider-region checks stay `UNVERIFIED` when the authoritative mapping is missing or ambiguous. The app does not invent a winning region.
- Dashboard totals must reconcile with source rows, distinct entities and findings.
