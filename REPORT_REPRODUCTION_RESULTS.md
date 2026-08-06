# Archived SU2 report-reproduction targets

These values were produced with the distributed sharp 720 x 181 Euler mesh and
SU2 v8.5.0 (Harrier): 600 first-order HLLC startup iterations followed by 2000
HLLC/MUSCL iterations with the Venkatakrishnan limiter. Each force statistic is
computed over the final 200 second-stage records.

| alpha (deg) | mean CL | CL peak-to-peak | mean CD | CD peak-to-peak | final log10 RMS(rho) | status |
|---:|---:|---:|---:|---:|---:|---|
| 0 | -3.9003e-12 | 1.8533e-11 | 0.02736429 | 1.4928e-05 | -4.15527 | QUALIFIED_PASS |
| 1 | 0.02451176 | 1.1583e-05 | 0.02781803 | 6.0443e-06 | -4.15172 | QUALIFIED_PASS |
| 2 | 0.04896847 | 2.3734e-05 | 0.02920966 | 1.9322e-05 | -4.14287 | QUALIFIED_PASS |
| 3 | 0.07354901 | 3.4313e-05 | 0.03158512 | 2.6866e-05 | -4.12943 | QUALIFIED_PASS |
| 4 | 0.09807290 | 3.7535e-05 | 0.03488493 | 1.4894e-05 | -4.11164 | QUALIFIED_PASS |

`QUALIFIED_PASS` is deliberately not named `CONVERGED` or `VERIFIED`. The
density residual plateaus near -4.1 and does not satisfy the configured -10
target. The stable-load windows, populated CL/CD ranges, zero physicality
warnings, and alpha-zero symmetry/wave-angle checks pass. One sharp grid cannot
establish grid independence.

Native-grid wave measurements used in the revised report are:

| case | branch | SU2 beta (deg) | shock-expansion beta (deg) | absolute error (deg) |
|---|---|---:|---:|---:|
| alpha 0 | upper/lower | 25.57891 | 25.61135 | 0.03244 |
| alpha 4 | upper | 23.43515 | 22.35442 | 1.08073 |
| alpha 4 | lower | 28.42638 | 29.25100 | 0.82462 |

Run the cases from the package root and regenerate the report assets:

```bash
for angle in 0 1 2 3 4; do
  python3 scripts/run_case.py "cases/euler_alpha${angle}" --threads 4
done
python3 -m pip install numpy scipy matplotlib pillow
python3 scripts/generate_su2_report_assets.py --out report_assets
```

If outputs already exist, preserve them with `--archive-old` or remove them
manually only after copying the original manifests. The report-asset script
uses native restart values for quantitative metrics. Interpolation is used for
display and for the documented polynomial surrogate only.
