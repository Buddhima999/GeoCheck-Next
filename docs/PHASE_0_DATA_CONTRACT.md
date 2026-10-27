# Phase 0 — Data Contract

The synthetic sample's 13 headers are now confirmed as the initial logical CSV contract. They match the current automatic header mapper. The actual production export still needs a sanitized-header check before a real import.

## Expected logical fields

| Field | Required for | Notes |
|---|---|---|
| Customer ID | Reliable entity-level reporting | Preserve original value; duplicates/conflicts must remain reviewable |
| Phone | Area validation | Optional; normalize carefully |
| Address | Address-zone validation | Optional; ambiguous localities must be unverified |
| Customer latitude/longitude | Customer spatial validation | Required for coordinate checks |
| DP ID | DP association checks | Normalize for grouping without losing original value |
| DP latitude/longitude | Distance checks | May come from source rows or an authoritative DP master |
| `line_distance_m` | Line-distance rules | Optional; user confirmed the unit is metres. No maximum threshold is configured until the provider approves one. |
| `overhead_distance_m` | Measurement validation | Optional; the column name suffix suggests metres but its business meaning is not confirmed. Do not compare with straight-line distance until confirmed. |

## Data concepts

- **Source row:** one CSV row. Preserve source row number and mapped/original values for reconciliation.
- **Customer entity:** a logical customer represented by its identifier, where reliable.
- **DP entity:** a logical DP represented by its normalized DP ID.
- **Observed DP coordinates:** DP coordinates recorded on one source row. These may conflict across rows.
- **Resolved geography:** administrative zone derived from trusted polygons; provider region assigned only from authoritative company data.
- **Finding:** one rule outcome for one relevant row or entity.

## Three core MongoDB collections

- `validation_runs`: file metadata/hash, mapping, thresholds, rule/reference versions, progress and summary.
- `customer_records`: one record per source row per run, original values, normalized fields, geography, bounded check results and issue codes.
- `dp_records`: one distinct DP per run, coordinate consistency summary, location result and customer-association aggregates.

Do not store all customers inside a DP document as an array. Keep customer records individually queryable via run and DP ID indexes.

## Required run metadata

Every run identifies its source file hash, filename, column mapping, import/validator version, rule version, reference-data versions, thresholds, counts, timestamps and final status.

## Inputs to confirm

1. A sanitized 20–50-row example using actual CSV headers. Include examples of missing values, duplicate/conflicting IDs, coordinate conflicts and distances if available. Replace personal data and precise coordinates with synthetic equivalents while preserving structure.
2. Provider's official DP/region mapping.
3. Approved LEA/telephone-area reference table and the number types to which it applies.
4. `line_distance_m` is confirmed in metres. The provider has confirmed that `overhead_distance_m` is the straight-line distance between the customer and DP; the comparison uses the configurable tolerance in validation settings.
5. Duplicate-customer policy and source-of-truth DP coordinates.
6. DP association threshold, minimum eligible-customer count and required pass rate.
7. Deployment mode: multiple authenticated users sharing MongoDB-backed runs is the initial design.

Until inputs are approved, ambiguous checks must return UNVERIFIED rather than inventing evidence.
