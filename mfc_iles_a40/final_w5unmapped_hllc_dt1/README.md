# Final MFC Mach-3/AoA-40 viscous no-model case

This directory archives the exact source and provenance files for the
successful two-dimensional MFC viscous/no-model (ILES-like) calculation of
Mach-3 flow over a diamond airfoil at 40 degrees angle of attack.  The run
started from the uniform initial condition at nondimensional time 0 and
completed at time 3.

This is a controlled two-dimensional scale-resolving calculation, not a claim
of a fully resolved three-dimensional LES.

## Verified configuration

- MFC commit: `0c9a1d434410175ac483b8d71646455444e3b7eb`
- grid: 2969 by 2699 Cartesian cells (`f270`)
- domain: `-5 <= x/c <= 6`, `-5 <= y/c <= 5`
- Mach number: 3
- angle of attack: 40 degrees
- chord Reynolds number: 1,000,000
- molecular viscosity enabled; no explicit RANS or SGS model
- no-slip immersed boundary
- third-order Runge--Kutta time integration
- time step: `1/5400`
- fifth-order **unmapped** WENO reconstruction
- HLLC Riemann solver with direct wave-speed estimates
- immersed-boundary neighborhood radius: 4 MPI ranks
- fourth-order viscous derivatives
- final step: 16200 (`t=3`)
- output interval: 270 steps (`Delta t_save=0.05`, 61 states)

The completed-run marker and final field audit are in `provenance/`.  Both the
last two audited states (`t=2.95` and `t=3`) passed finite-value and CFL-proxy
checks.  The final audit reported a CFL proxy of approximately 0.3703.

## Files

- `case.py`: exact parameterized MFC case used for the successful run.
- `Diamond_Airfoil_2D_MFC.stl`: planar two-facet diamond geometry.
- `run_initial_full_stage.sbatch`: exact Unity stage driver used for the
  successful start-from-zero production calculation.
- `make_recovery_movies.py`: post-processing and movie-generation script.
- `provenance/final-case-expanded.json`: fully expanded parameters for the
  `f270`, `t=0..3` production invocation.
- `provenance/RUN_OK_INITIAL.txt`: completion marker from the successful run.
- `provenance/mfc-iles-a40-initial-field-audit.json`: field-health audit.
- `provenance/mfc-iles-a40-initial-movie-manifest.json`: movie scales, crop,
  steps, and field sources.
- `provenance/MFC.generated.sh`: MFC-generated execution script retained only
  for provenance.  It contains absolute Unity paths and is not portable.

## Exact case expansion

```bash
python3 case.py \
  --mode initial --grid f270 --start-time 0 --final-time 3.0 \
  --save-dt 0.05 --dt-factor 1 --format binary
```

The Slurm driver expects an already prepared MFC tree and these environment
variables: `STAGE`, `CASE_DIR`, `MFC_ILES_ROOT`, `START_TIME`, `STOP_TIME`,
`SAVE_DT`, `DT_FACTOR`, and `FPS`.  For the archived production run their
values were `initial`, the case directory, the pinned MFC tree, `0`, `3.0`,
`0.05`, `1`, and `2`, respectively.  The job used 32 MPI ranks and 96 GiB.

The large restart/Silo fields are not stored in GitHub.  They remain necessary
to continue the calculation from `t=3`.

## Historical stability note

Earlier files in the parent `mfc_iles_a40` directory used mapped WENO5 with
HLLC and failed reproducibly near `t=0.403`.  Reducing the time step did not
remove that failure.  The completed result archived here used unmapped WENO5
and an IB neighborhood radius of four.  Use this directory, rather than the
older recovery case, when reproducing or citing the successful calculation.
