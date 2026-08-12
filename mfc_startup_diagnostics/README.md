# MFC A40 high-cadence startup diagnostic

This is a short diagnostic rerun of the validated MFC Euler/IBM f270 case:

- Mach 3, angle of attack 40 degrees
- 2970 x 2700 cells, `dt = 1/5400`
- 4320 steps, `t_stop = 0.8`
- save every 108 steps, `Delta(t_save) = 0.02`
- 41 stored states including the initial condition
- same RK3/WENO5/HLLC numerics and two-triangle planar diamond STL as the completed f270 run

The old interval was 2916 steps (`Delta(t_save)=0.54`), so the bow-shock
formation occurred almost entirely between its first two frames. The new
interval resolves that formation while keeping the rerun short.

## Submit on Unity

From the `SU2-Diamond-Airfoil-Verification` repository root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-startup-diagnostics/mfc_startup_diagnostics/unity_submit_startup.sh)
```

The default request is one CPU node, 32 MPI tasks, 120 GiB, and three hours.
The successful f270 timing implies about 75--90 minutes for the simulation,
plus post-processing and compact packaging.

The Unity wrapper explicitly selects `mpirun`. Allowing MFC to auto-select
`srun` with OpenMPI 5 caused `MPI_Init` to fail before `syscheck` on Unity.

The submitter can be adjusted without editing it. For example, 81 states at
`Delta(t_save)=0.01` (larger raw output) use:

```bash
SAVE_EVERY=54 bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-startup-diagnostics/mfc_startup_diagnostics/unity_submit_startup.sh)
```

## What to upload

The raw `restart_data/` and `silo_hdf5/` directories stay on Unity. After a
successful job, upload only the files in the printed `UPLOAD_DIR`:

- every `MFC_A40_STARTUP_*.part`
- `PARTS.sha256`
- `ORIGINAL.sha256`

The compact package contains pressure, density, both velocity components, the
IB mask, and IB force history on a stride-3 near-field crop. This is sufficient
to derive Schlieren, vorticity, streamlines, shock motion, vortex trajectories,
and force histories. Output Silo is single precision to halve visualization
storage; the solver numerics remain the same as the completed double-precision
f270 run.

## Monitor

The submitter prints `RUN_BASE`, `CASE_DIR`, and `JOB_ID`. Then:

```bash
source "$RUN_BASE/submission.env"
squeue -j "$JOB_ID"
tail -f "$CASE_DIR/slurm-${JOB_ID}.out"
```

Completion requires both `RUN_OK.txt` and the log line reporting four detected
2-D model boundary edges.
