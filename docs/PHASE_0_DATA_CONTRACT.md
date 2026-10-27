# Phase 0 — Customer Audit Data Contract

The current upload profile is the nine-field provider CSV contract below. The actual provider export must still be checked against this logical schema before production use.

## Required CSV fields

| Source header / logical field | Required | Validation purpose |
|---|---:|---|
| `customer_id` | Yes | Preserve source value; detect conflicting records for the same normalized ID |
| `router_number` | Yes | Customer fixed-line telephone number; validate Sri Lankan 10-digit format and infer a numbering area from its area code. This does not identify exact coordinates or subscriber ownership |
| `customer_latitude` / `customer_longitude` | Yes | Validate numeric geographic coordinates, placeholders, possible swaps and country-boundary membership |
| `cust_address` (logical `address`) | Yes | Compare the geocoded/reference location with the uploaded customer coordinate where a trusted lookup is available |
| `dp_id` | Yes | Preserve original value and normalize only for grouping |
| `dp_latitude` / `dp_longitude` | Yes | Check coordinate validity, repeated-DP consistency and distance to customer |
| `line_distance_m` | Yes | Validate that the value is finite, numeric and non-negative; no cable-length maximum is assumed without provider approval |

The importer normalizes header capitalization, punctuation and spacing. It recognizes `cust_address` as `address`, and common aliases for coordinates and telephone number. A legacy `phone_number` column can substitute for `router_number` if it is the source's actual fixed-line telephone field.

## Calculated outputs

- `overhead_distance_m`: calculated from customer and DP coordinates using the Haversine formula. The existing project data contract defines this as direct geographic distance, not the measured cable path or a Google road route.
- `overhead_distance_500m_status`: whether that calculated direct distance is at most the configured `CUSTOMER_DP_LIMIT_M` (default 500 m).
- `google_route_distance_m`: only returned when optional Google Routes API mode is enabled and Google returns a valid driving route. This is a road-route distance, not overhead cable distance.
- `distance_500m_status`: result for the configured distance rule. In the default `straight_line` mode it matches the direct-coordinate threshold; with optional `google_routes` mode it is the route-distance threshold. The direct overhead threshold is reported separately in both modes.
- `customer_inside_sri_lanka`: `PASS`, `FAIL`, or `UNVERIFIED` based on the configured country polygon and valid customer coordinates.
- `address_match_status`: outcome of Google Geocoding (if enabled) or an approved locality-zone reference. It remains unverified when no suitable source is available.
- `router_number_format_status`, `router_number_area_code`, and `router_number_area`: format validation and inferred numbering area for the fixed-line number.
- `phone_area_status`: whether the inferred telephone numbering area is consistent with address/locality labels or a configured coordinate-to-area reference. Text-only consistency is not exact geolocation.
- `overall_status`, `issue_codes`, and `findings`: row-level fault summary and traceable rule explanations.

## Status semantics

- `PASS`: evidence satisfies the configured check.
- `FAIL`: evidence confirms a rule violation.
- `UNVERIFIED`: the necessary reference, valid coordinates or external response is unavailable or ambiguous.
- `NOT_APPLICABLE`: the rule does not apply to the row.

Fault exports filter `overall_status=FAIL`; unverified reports remain separate so lack of evidence is not reported as a confirmed fault. Multiple findings are preserved for a row, while customer totals count each row once.

## Reference data and Google Maps

The bundled country boundary was copied from the user's earlier `Buddhima999/geocheck-vercel` project and has not been independently certified. Its source, license, coastline precision, island coverage and inland-water treatment must be reviewed with the provider. It is sufficient for initial development tests, not authoritative evidence of a legal boundary.

Google Routes / Geocoding are optional, use backend-only API credentials, and have a per-run request limit. Routes and Geocoding APIs must be enabled and billing configured in the relevant Google Cloud project. Google requests can incur costs, and the 500 m overhead threshold is not derived from road-route length. See `docs/CUSTOMER_DP_AUDIT.md`.

## Still to confirm with the provider

1. The official source of truth for customer IDs, router numbers and DP IDs.
2. Whether `router_number` is a hardware serial, a service identifier, or a telephone number.
3. The required measurement semantics for `line_distance_m`; cable lengths must not be forced to equal Haversine or road-route distance.
4. Whether Google Maps route distance is needed as an additional check, and an approved request budget for the expected dataset size.
5. The official administrative area / LEA / provider-region mappings, if geographic number and locality checks are needed.
6. Duplicate-customer policy, authoritative DP coordinates, minimum eligible DP-customer count and required pass rate.

Until those inputs are approved, ambiguous checks stay `UNVERIFIED` rather than inventing evidence.
