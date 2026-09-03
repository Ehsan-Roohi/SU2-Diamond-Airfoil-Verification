# SU2 Mach-3 angle-of-attack results

Source campaign: `20260903T023310Z_536e17603907`  
Overall audit label: **MIXED_OR_FAILED_GATE_FIELDS_RETAINED**

| AoA (deg) | fail-closed status | mean CL (last window) | mean CD (last window) | final log10 RMS(rho) |
|---:|---|---:|---:|---:|
| 0 | NUMERICAL_GATE_PASS | 5.100251e-12 | 0.02736429 | -4.155273 |
| 4 | NUMERICAL_GATE_PASS | 0.0980729 | 0.03488493 | -4.111638 |
| 8 | FIELD_RETAINED_ACCEPTANCE_UNKNOWN | 0.1969057 | 0.05750682 | -4.006201 |

`NUMERICAL_GATE_PASS` means only that the configured numerical checks passed.
It does **not** establish grid independence. `FIELD_RETAINED_NUMERICAL_GATE_FAILED`
means that a native field exists and is useful for diagnosis/ML, but its forces
and shock metrics must not be presented as an accepted validation result.

## Compact evidence

- `fields_comparison.png`: density and Mach snapshots on the common grid;
- `aerodynamic_histories.png`: CL, CD, and density-residual histories;
- `aerodynamic_summary.csv` and `summary.json`: machine-readable statistics;
- `cases/*/aerodynamic_history.csv`: standardized histories;
- `cases/*/case_metrics.json` and `shock_ridge_*.csv`: copied when produced;
- `provenance/`: solver/run identity and capture markers.

The large restart CSV and VTU fields remain in the checksummed Unity archive;
they are intentionally not committed to ordinary Git history.
