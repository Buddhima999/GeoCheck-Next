# Backend validation tests

From the `backend` directory, run:

```powershell
python -m unittest discover -s tests -v
```

The tests use Python's standard library; no MongoDB service is required for the pure-rule tests.

The supplied synthetic 50-row CSV and expected-findings CSV are currently downloaded test fixtures, not production data. Place them under the repository's `test-data/` folder for fixture-based integration tests. Do not commit real customer data.

Important semantics:
- `FAIL` means a configured rule has evidence of a violation.
- `UNVERIFIED` means an authoritative reference is unavailable or ambiguous.
- `NOT_APPLICABLE` means the rule cannot apply to this record.
- Possible coordinate swaps suppress dependent distance/geography checks to avoid cascading false errors.
- `validate_dataset_small` intentionally holds records in memory and is for small test fixtures only. It must not be used for the 600,000-row production import; cross-row checks there must use MongoDB-backed streaming/batched processing.
- The default 500 m customer-DP limit, 50 m DP-coordinate conflict tolerance, and 25 m overhead-distance tolerance are configurable starting values, not provider-approved rules.
