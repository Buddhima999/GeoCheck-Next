# Bounded-batch validation service

## What is implemented

The service in `backend/app/validation/batch_processor.py` reads the CSV through the streaming import helper and accumulates only one configured batch in application memory.

For every batch it:

1. Maps the source headers to the logical fields.
2. Runs row-level checks while preserving the original source values and row number.
3. Inserts row results into `customer_records`.
4. Upserts per-DP rollups into `dp_records`, comparing observed DP coordinates with the first valid observed location for that DP.
5. Updates progress counters and issue counts in `validation_runs`.
6. Marks the run `COMPLETED` when all rows finish, or `FAILED` when an exception occurs.

The three core collections remain separate. A DP document contains counts and the first observed coordinate, not an unbounded list of every customer.

## DP association eligibility rule

A row is excluded from DP association scoring when the customer location has a confirmed location-error code, such as missing/non-numeric coordinates, a possible coordinate swap, a country-boundary violation, a confirmed address-zone mismatch, a confirmed phone-area mismatch or a reported-geography mismatch.

A customer-to-DP distance failure is not itself used to disqualify the customer coordinates: it may be the relationship problem the DP analysis is intended to find.

The current eligibility count is a count of source rows, not deduplicated customer entities. Duplicate-customer identity resolution and final DP pass/fail scoring are not implemented yet.

## Reference-data behavior

When approved reference data is absent, reference-dependent checks remain `UNVERIFIED`. The service does not infer provider regions or telephone-area mappings.

The confirmed `line_distance_m` unit is metres. It is checked for a finite, non-negative value; an upper limit is not applied until the provider approves one. The provider confirmed `overhead_distance_m` represents straight-line customer-to-DP distance, so the validator compares it with the Haversine-derived distance. The 25 m tolerance remains provisional pending operational approval.

## Current limitations

- The caller must create the run record and provide a path to a saved CSV plus a column mapping.
- A web upload endpoint and background queue have not yet been connected.
- Progress is stored per batch, but safe restart/resume is not implemented yet. Restarting a partial run may collide with the unique source-row index; do not manually restart the same run.
- The service is not yet benchmarked at 600,000 rows.
- A DP's conflict check compares each new point with the DP's first valid observation. It does not compute the exact maximum pairwise distance over every observation.
- MongoDB integration has unit tests using in-memory collection fakes, but no live MongoDB integration test has been run.
- The provider's boundary tolerance, official regions, LEA table and overhead-distance meaning remain unconfirmed.

## Verification

The GitHub Actions workflow runs Python unit tests. It does not prove live MongoDB behavior or large-file performance. The scale target must be tested with generated synthetic data in a controlled environment before the system is treated as production-ready.
