# Backend validation tests

## Run locally on Windows

From the repository root:

```powershell
python -m unittest discover -s backend/tests -p "test_*.py" -v
```

Or from the `backend` directory:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

These pure-rule tests do not require a running MongoDB service. The GitHub Actions workflow runs the repository-root command for pushes to `main` and `feature/**` branches and for pull requests targeting `main`.

## Test data and safe use

The synthetic 50-row CSV and expected-findings CSV are downloaded test fixtures, not production data. Keep synthetic fixtures in `test-data/`; **never commit real customer names, phone numbers, addresses or precise coordinates**.

## Important rule semantics

- `PASS`: evidence supports the rule.
- `FAIL`: evidence demonstrates a violation.
- `UNVERIFIED`: authoritative reference data is missing or ambiguous.
- `NOT_APPLICABLE`: the rule cannot apply to this record.
- Possible coordinate swaps suppress dependent distance/geography checks to avoid cascading findings.
- `validate_dataset_small` holds records in memory and is only for small fixtures. It must not be used for the 600,000-row production import; cross-row checks need MongoDB-backed streaming/batched processing.
- The default 500 m customer-DP limit, 50 m DP-coordinate tolerance, and 25 m overhead-distance tolerance are configurable starting values, not provider-approved rules.
