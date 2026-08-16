# MFC f405 grid-convergence run for the Mach-3/AoA-40 case

This workflow adds the third systematically refined grid required to assess
spatial convergence of the MFC Euler/immersed-boundary result:

| Level | Cells/chord | Cartesian cells | Grid-spacing ratio |
|---|---:|---:|---:|
| f180 | 180 | 1980 x 1800 | -- |
| f270 | 270 | 2970 x 2700 | 1.5 |
| f405 | 405 | 4455 x 4050 | 1.5 |

The f405 case retains the f270 domain, Mach number, angle of attack, planar
two-triangle STL, Euler slip wall, RK3/WENO5/HLLC numerics, physical end time
`t=13.5`, and physical save interval `Delta(t)=0.54`.  Its stable explicit
step is `dt=1/8100`, giving 109350 time steps and 25 save intervals.
The submitter also requires the same MFC commit used by the validated runs,
`0c9a1d434410175ac483b8d71646455444e3b7eb`.

## Recommended Unity submission: three restartable jobs

The recommended production path splits f405 into three checkpoint-aligned
jobs.  Each requests 48 MPI ranks, 120 GiB, and 24 hours; the default QOS is
used because every segment is shorter than Unity's normal limit.  The measured
f270 peak was only 20.63 GiB, so scaling by the 2.25 grid-size ratio predicts
about 46.4 GiB for f405 and leaves a large memory margin.

The segment boundaries are:

| Segment | Step range | Physical-time range | Dependency |
|---|---:|---:|---|
| 1 | 0--34992 | 0--4.32 | optional prior `mfc-a40-*` job |
| 2 | 34992--69984 | 4.32--8.64 | `afterok` segment 1 |
| 3 | 69984--109350 | 8.64--13.5 | `afterok` segment 2 |

Run this on a Unity login node:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-f405-grid-study/mfc_grid_convergence/unity_submit_f405_chain.sh)
```

The first segment can use `afterany` for an older MFC job, but all scientific
segments use `afterok`: a missing/failed checkpoint prevents the next segment
from starting.  All three jobs write to the same new timestamped case
directory and the final stage alone writes `RUN_OK_F405.txt`.

## Legacy single-job submission

Run this on a Unity login node:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-f405-grid-study/mfc_grid_convergence/unity_submit_f405.sh)
```

The legacy request is 48 MPI ranks, 120 GiB, 72 hours, the `long` QOS, and an
Intel AVX-512 node.  The architecture constraint avoids the cross-architecture illegal
instruction previously observed when a binary built on an AVX-512 Intel node
was scheduled on a different CPU family.

The submitter automatically detects active `mfc-a40-*` jobs and adds an
`afterany` Slurm dependency.  Thus, the f405 job remains pending until the
current f270 continuation exits.  It also:

- creates a new timestamped `mfc_runs/fixed_ib_a40_f405_jfm_*` directory;
- never writes into an existing f180/f270 result directory;
- uses the installed MFC binaries with `--no-build`;
- takes a shared runtime lock so an MFC builder cannot replace binaries during
  the run;
- checks the exact grid, time step, end time, output precision, planar STL,
  MFC commit, and four detected 2-D immersed-boundary edges;
- requires the final restart and writes `RUN_OK_F405.txt` before reporting
  success.

To depend on a specific job explicitly:

```bash
AFTER_JOB=12345678 bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-f405-grid-study/mfc_grid_convergence/unity_submit_f405.sh)
```

Use `AFTER_JOB=none` only when no other native MFC run or build is active.

## Monitoring

The submitter prints `RUN_BASE`, `CASE_DIR`, `ENV_FILE`, and `JOB_ID`:

```bash
source "$ENV_FILE"
squeue -j "$JOB_ID" -o "%.18i %.18j %.2t %.10M %.20R"
sacct -j "$JOB_ID" -X \
  --format=JobIDRaw,JobName%20,State,ExitCode,Elapsed,MaxRSS,NodeList
tail -f "$CASE_DIR/slurm-${JOB_ID}.out"
```

Publication analysis should compare common late-time windows and identical
physical save times across f180/f270/f405.  Report load means and uncertainty,
bow-shock stand-off, reverse-flow area, and large-scale RMS measures.  Treat
individual small wake-vortex phase, count, and peak vorticity separately: a
shock-capturing Euler calculation can converge in large-scale observables while
remaining phase-sensitive at the smallest resolved scales.

## Compact f405 package for analysis and movies

After all three f405 segments complete, submit the lightweight packaging job:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-f405-grid-study/mfc_grid_convergence/unity_submit_pack_f405.sh)
```

It places one timestamped `MFC_A40_F405_MOVIE_READY_*.zip` plus its SHA-256
file in the repository root.  The ZIP retains all 26 physical snapshots from
`t=0` to `13.5` on a stride-6 near-field/wake crop and includes pressure,
density, velocity, the immersed-boundary mask, force arrays, logs, and run
metadata.  Raw Silo and restart files remain on Unity.  Schlieren, vorticity,
and streamlines are derived from the packed primitive fields.

## Recover f405 forces without rerunning MFC

The original compact-package script could silently leave the force arrays as
`NaN` when MPI rank 0 did not overlap the selected movie crop.  The corrected
packer reads rank-0 loads independently of spatial cropping and refuses to
create a package if any saved load is non-finite.

For an already completed f405 chain, recover the physical force history
directly from MFC's `restart_data/ib_state_*.dat` records.  This does not rerun
the solver and creates only a small CSV, JSON summary, ZIP, and SHA-256 file:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-f405-grid-study/mfc_grid_convergence/unity_recover_f405_forces.sh)
```

The command prints `UPLOAD_THIS` and `UPLOAD_SHA256` when it finishes.  Drag
is resolved along the Mach-3 freestream at 40 degrees and lift along its
counterclockwise normal, then both are normalized by
`0.5 * rho_inf * U_inf^2 * chord`.

## Three-grid force convergence and f608 decision

Once the f180, f270, and f405 runs are complete, recover all three force
histories and evaluate the common late-time window with one login-node command:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-f405-grid-study/mfc_grid_convergence/unity_recover_a40_grid_convergence.sh)
```

The tool locates complete 26-state cases by their expected final IB-state
records, verifies every physical time, applies the same force rotation and
normalization, and reports the f180/f270/f405 means.  For monotonic convergence
it also computes observed order, Richardson extrapolation, and fine-grid GCI.
It recommends `STOP_AT_F405` only when both load coefficients are monotonic and
the f270-to-f405 change and f405 GCI are each at most one percent; otherwise it
recommends `RUN_F608`.  The final ZIP and checksum paths are printed as
`UPLOAD_THIS` and `UPLOAD_SHA256`.

All-zero force histories are rejected rather than treated as coarse-grid data.
For older runs whose `ib_state_*.dat` records contain zero loads, the tool uses
the `ib_force_x` and `ib_force_y` variables in the rank-0 Silo snapshots as a
validated fallback and records the selected force source in its JSON output.

If both the historical IB-state and rank-0 Silo loads are zero, do not use
that grid in Richardson/GCI calculations.  Submit the clean f180 repair run:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-f405-grid-study/mfc_grid_convergence/unity_submit_f180_force_repair.sh)
```

This is a full but inexpensive f180 rerun on the same pinned MFC commit and
the same physical interval and output cadence as f270/f405.  Its batch job
will not write the completion marker unless all 26 IB-state records exist and
the late-time force signal is finite and nonzero.  After it completes, rerun
the three-grid recovery command above; the newest valid f180 case is selected
automatically.

## f608 production follow-up

The repaired, nonzero f180 force history gives the following common-window
load sequence at `t >= 8.64`:

| Metric | f180 | f270 | f405 | f270-to-f405 | Observed order | f405 GCI |
|---|---:|---:|---:|---:|---:|---:|
| CD | 0.823644 | 0.838508 | 0.850956 | 1.463% | 0.437 | 9.423% |
| CL | 0.913784 | 0.934102 | 0.949090 | 1.579% | 0.750 | 5.550% |

The sequence is monotonic, but the last-grid change exceeds one percent and
the low observed orders show that the loads are not yet demonstrably in the
asymptotic range.  A fourth level is therefore warranted.  The `f608` label
denotes the exact next target of 607.5 cells per chord.  Integer Cartesian
counts give `6682 x 6075` cells, with `dt=1/12150`, 164025 steps, and the same
26 physical output times from `t=0` to `13.5` at `Delta(t)=0.54`.

The production submitter uses five `afterok`-chained segments.  Each segment
covers `Delta(t)=2.7`, requests 48 MPI ranks, 120 GiB, and 48 hours on one CPU
node, and writes a restart before the next segment can begin.  The memory
request follows the measured f270 peak scaled by grid area (about 104.5 GiB
predicted for f608).  Scaling the completed f180 runtime by total cell-step
work predicts about 165 wall-clock hours overall, or about 33 hours per
segment.  No QOS is requested by default.

Run this on a Unity login node:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-f405-grid-study/mfc_grid_convergence/unity_submit_f608_chain.sh)
```

The submitter verifies the immutable case and STL SHA-256 digests, the pinned
MFC commit, all five parameter sets, checkpoint existence, four immersed-body
edges, all 26 final IB-state records, physical times, and a finite nonzero
late-time load.  Only the fifth successful segment writes
`RUN_OK_F608.txt`.  To delay the first segment explicitly, prefix the command
with `AFTER_JOB=<job-id>`; otherwise the default is `AFTER_JOB=none`.
