# Synthetic test data

All records in this folder are artificial test fixtures. They are not real customers, addresses, phone numbers, provider regions, or network locations.

- `GeoCheck_Next_Synthetic_Sample_50.csv`: small correctness fixture (50 source rows).
- `GeoCheck_Next_Synthetic_Sample_Expected_Findings.csv`: row-numbered cases deliberately injected into the sample.
- `GeoCheck_Next_Synthetic_Scale_Manifest.json`: describes the larger, locally downloadable 600,000-row performance fixture.

The sample includes malformed/missing coordinates, plausible coordinate swaps, an offshore point, distance anomalies, phone/address mismatches, duplicate customer IDs and a DP coordinate conflict. Not every result can be decided without the official geographic/LEA/provider reference data; those checks should remain UNVERIFIED until such references are configured.

The 600k CSV is deliberately not committed to Git because it is large. Download it from the ChatGPT artifact links in the project setup conversation, or regenerate it using the companion generator when available.

**Never use synthetic data as a real provider dataset or call the synthetic phone patterns.**