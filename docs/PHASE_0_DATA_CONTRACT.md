# Phase 0 — Data Contract

This proposal is not a claim about the provider's exact CSV columns. Final mapping must be confirmed against a sanitized sample of the real file.

## Expected logical fields

| Field | Required for | Notes |
|---|---|---|
| Customer ID | Reliable entity-level reporting | Preserve original value; duplicates/conflicts must remain reviewable |
| Phone | Area validation | Optional; normalize carefully |
| Address | Address-zone validation | Optional; ambiguous localities must be unverified |
| Customer latitude/longitude | Customer spatial validation | Required for coordinate checks |
| DP ID | DP association checks | Normalize for grouping without losing original value |
| DP latitude/longitude | Distance checks | May come from source rows or an authoritative DP master |
| Line distance | Line-distance rules | Optional; units/meaning require confirmation |
| Overhead distance | Measurement comparison | Optional; units/meaning require confirmation |

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
4. Meaning and units of line/overhead distance values.
5. Duplicate-customer policy and source-of-truth DP coordinates.
6. DP association threshold, minimum eligible-customer count and required pass rate.
7. Deployment mode: multiple authenticated users sharing MongoDB-backed runs is the initial design.

Until inputs are approved, ambiguous checks must return UNVERIFIED rather than inventing evidence.
