# GeoCheck Next — Validation Rules

## Status semantics

- `PASS`: sufficient evidence supports the rule.
- `FAIL`: sufficient evidence demonstrates a violation.
- `UNVERIFIED`: insufficient or ambiguous evidence.
- `NOT_APPLICABLE`: rule does not apply to this record.

Missing results must never default to PASS.

## Proposed rule catalogue

| Code | Entity | Rule | Notes |
|---|---|---|---|
| `COORD_MISSING` | Customer / DP observation | Required coordinates missing | Skip spatial checks |
| `COORD_NOT_NUMERIC` | Customer / DP observation | Cannot parse coordinate | Fail |
| `COORD_OUT_OF_RANGE` | Customer / DP observation | Latitude outside [-90, 90] or longitude outside [-180, 180] | Fail |
| `COORD_ZERO_PLACEHOLDER` | Customer / DP observation | Both coordinates zero | Flag placeholder |
| `COORD_POSSIBLE_SWAP` | Customer / DP observation | Swapped values make geographic sense while originals do not | Warning; never auto-correct |
| `COUNTRY_BOUNDARY` | Customer / DP | Outside approved accepted geography | Approved 150 m coastal tolerance, metre-based geometry |
| `PHONE_AREA_MISMATCH` | Customer | Geographic phone area conflicts with location-derived area | Apply approved LEA table; normally a review finding |
| `ADDRESS_ZONE_MISMATCH` | Customer | Coordinates outside reliably resolved locality zone | Unverified if locality ambiguous or zone unavailable |
| `CUSTOMER_DP_DISTANCE` | Customer-DP relationship | Straight-line distance exceeds threshold | Association anomaly; use only eligible customer evidence for DP scoring |
| `OVERHEAD_DISTANCE_MISMATCH` | Distance field | Reported straight-line customer-to-DP distance differs beyond approved tolerance from coordinates | Enabled by default; provider confirmed the semantics. The 25 m tolerance remains provisional pending approval |
| `LINE_DISTANCE_INVALID` | Distance field | Non-numeric, negative or invalid value | Invalid line distance must never pass merely because it is below threshold |
| `DP_COORD_CONFLICT` | DP | Same normalized DP ID has materially different coordinate observations | Report groups/rows; never choose a winner automatically |
| `CUSTOMER_ID_CONFLICT` | Source row/customer | Same normalized customer ID has incompatible source records | Preserve each row |

## Distance semantics

Haversine estimates straight-line separation in metres; it does not calculate actual cable routing.

- Customer-to-DP distance: initial proposal is 500 m; confirm with the provider.
- Reported overhead distance: the provider confirmed it is straight-line customer-to-DP distance. Compare it to the Haversine-derived distance using `overhead_distance_tolerance_m` (currently a provisional 25 m tolerance that still needs operational approval).
- `line_distance_m` is confirmed to use metres. Validate that values are finite and non-negative; no maximum threshold is applied until the provider approves one. Do not require cable length to equal straight-line distance.

## Geographic tolerance

The proposed 150 m coastal tolerance must be applied using geometry in a suitable metre-based coordinate system, not by adding 150 to latitude/longitude. Country boundary, inland water and locality-zone tests are separate rules.

## Aggregation

- A row with several findings counts once toward rows-with-errors; each finding remains individually queryable.
- Customer and DP metrics report passing, failing and unverified counts.
- DP physical-location quality and DP-customer association quality are separate.
- Customers with confirmed location errors are ineligible to count as evidence against DP association quality.
- Customer IDs are checked across the completed source run after batch processing. Rows sharing an ID are marked `CUSTOMER_ID_CONFLICT` when rounded coordinates (5 decimal places), trimmed/case-normalized address, or phone digits differ; identical repeated signatures are preserved without this conflict finding.
- Provider-region checks stay `UNVERIFIED` when the authoritative mapping is missing or ambiguous. The app does not invent a winning region.
- Define minimum eligible DP customers and required pass percentage before implementing final aggregate DP pass/fail.
- Dashboard totals must reconcile with source rows, distinct entities and findings.
