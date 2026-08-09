# SU2 time-accurate Euler wake check

This research-control case tests whether SU2 v8.5.0 reproduces the unsteady
vortex/entropy-sheet roll-up observed in the independent MFC Euler calculation
for the Mach-3 sharp diamond at `alpha=30 deg`.

It is intentionally different from the earlier steady SU2 cases:

- `TIME_DOMAIN=YES` with second-order dual-time stepping;
- a sharp body-fitted `1440 x 721` O-grid (1,036,800 cells);
- HLLC plus second-order MUSCL/Venkatakrishnan reconstruction;
- a physical step of `2e-6 s` and 4,000 steps, about 8.3 chord-convection
  times for a one-metre chord at Mach 3 and 300 K;
- snapshots every 90 steps, approximately `0.187` chord-convection time apart,
  close to the MFC snapshot spacing.

The 721 radial points are important. At `x/c=2`, the wake-ray streamwise
spacing is approximately `0.011c`; a simple `1440 x 361` doubling would leave
approximately `0.024c` there. The grid remains an O-grid rather than a formal
C-grid, so this run is a strong cross-check, not a final wake-grid GCI study.

## Unity submission

`SU2_CFD` must either be on the submitted environment's `PATH`, be available
from a Unity module named `su2/8.5.0` (or a capitalization variant), or be
specified explicitly:

```bash
export SU2_CFD_BIN=/absolute/path/to/SU2_CFD
```

Submit from the repository root:

```bash
bash su2_unsteady_euler/unity_submit_su2_unsteady.sh
```

The job first generates and validates the one-million-cell mesh, advances a
1,000-iteration first-order steady initializer, and then launches the
second-order dual-time calculation. Each job uses an isolated directory:

```text
su2_runs/alpha30_euler_wakefine_unsteady/job_<SLURM_JOB_ID>/
```

Completion requires `SU2_UNSTEADY_RUN_COMPLETE=1`. The run writes load history,
numbered ASCII restarts, numbered VTU volume snapshots, and surface CSV files.
Interpret wake structure using instantaneous density-gradient fields plus
temporal mean/RMS and spectra; do not describe the Euler wake as viscous
boundary-layer separation.
