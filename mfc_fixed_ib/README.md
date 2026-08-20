# Corrected MFC Mach-3 diamond-airfoil runs at 40 degrees

The completed fine run `62733783` must not be used as a physical result. Its
log reported:

```text
Number of 2D model boundary edges: 0
```

The old STL was a three-dimensional extruded prism. MFC's two-dimensional STL
reader projects triangle edges into the x-y plane and removes repeated interior
edges; the prism therefore collapsed to zero model-boundary edges. The flow
field remained freestream even though Slurm reported `COMPLETED`.

This directory fixes the problem with a planar, two-triangle STL at `z=0`. The
static validator and the post-run log gate both require exactly four projected
boundary edges. Fine or very-fine production jobs are submitted only after a
smoke job succeeds.

## Resolution matrix

| Preset | Cells/chord | Cartesian cells | Steps | dt | Final time | Save interval |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fine` | 180 | 1980 x 1800 | 48,600 | 1/3600 | 13.5 | 1,944 steps = 0.54 |
| `very-fine` | 270 | 2970 x 2700 | 72,900 | 1/5400 | 13.5 | 2,916 steps = 0.54 |

Both production grids use the domain `x=[-5,6]`, `y=[-5,5]`, Mach 3, Euler
slip-wall immersed-boundary treatment, RK3/WENO5/HLLC, and 40-degree incidence.

The scripts expect the verified MFC checkout at:

```text
SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43
```

corresponding to MFC commit
`0c9a1d434410175ac483b8d71646455444e3b7eb`.

## Unity launch

From `SU2-Diamond-Airfoil-Verification`:

```bash
# Fine only (f180), after its smoke gate
bash mfc_fixed_ib/unity_submit_fixed_mfc.sh fine

# Very fine only (f270), after its smoke gate
bash mfc_fixed_ib/unity_submit_fixed_mfc.sh very-fine

# Or submit one smoke gate followed by both production jobs
bash mfc_fixed_ib/unity_submit_fixed_mfc.sh both
```

Each invocation prints `RUN_BASE`, the submitted Slurm job IDs, and the path to
`submission.env`. A production job exits with code 40 if MFC does not report
exactly four boundary edges and with code 41 if the final Silo collection is
missing.

## Status and final outputs

```bash
source /path/printed/by/the/launcher/submission.env
sacct -j "$SMOKE_JOB,$FINE_JOB,$VERY_FINE_JOB" -X \
  --format=JobIDRaw,JobName%20,State,ExitCode,Elapsed,MaxRSS,NodeList

find "$RUN_BASE" -type f \
  \( -name 'RUN_OK.txt' -o -name 'collection_48600.silo' \
     -o -name 'collection_72900.silo' -o -name 'mfc-*.log' \) -print
```
