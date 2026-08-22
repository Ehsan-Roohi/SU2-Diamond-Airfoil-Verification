# MFC viscous no-model screen: Mach 3, angle of attack 40 degrees

This research workflow tests whether the coherent vortex train in the MFC
Euler/slip-wall solution survives after adding molecular viscosity and a
no-slip wall in the same code, geometry, domain, grids, and RK3/WENO5/HLLC
framework.  It is a controlled two-dimensional **ILES-like screening run**,
not a claim of a fully resolved three-dimensional LES.

The production default is f270 from nondimensional time 0 to 3.  It writes 61
real fields at `Delta(t)=0.05`.  Every saved state retains density, pressure,
both velocity components, out-of-plane vorticity, numerical Schlieren, and the
immersed-boundary state.  The launcher never prunes earlier fields and fails if
all 61 restart and Silo states are not present.

Physics and numerics:

- Mach 3, angle of attack 40 degrees, `Re_c=1e6`;
- MFC single-fluid viscous equation set (`model_eqns=2`);
- no explicit RANS or SGS model;
- no-slip immersed diamond (`patch_ib(1)%slip=F`);
- RK3, WENO5/HLLC, WENO viscous-flux reconstruction, fourth-order derivatives;
- an isolated MFC build tree so the existing compile-time-optimized Euler
  executables and running jobs are not modified;
- a portable `x86-64-v3` compiler baseline (instead of MFC's Release
  `-march=native`) so smoke and production binaries remain valid when Slurm
  places them on different Unity CPU models.

Run on a Unity login node:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/mfc-a40-iles-screen/mfc_iles_a40/unity_submit_iles.sh)
```

The command submits a small viscous smoke/build job and an `afterok` f270
production job.  It prints both job IDs and `submission.env`.  To request the
larger f405 screen instead:

```bash
GRID=f405 bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/mfc-a40-iles-screen/mfc_iles_a40/unity_submit_iles.sh)
```

When successful, the production directory contains `RUN_OK_MFC_ILES.txt` and
`FIELD_INVENTORY.tsv`, in addition to all restart and Silo fields.  These are
the inputs for matched vorticity/streamline and shock/Schlieren movies.

## Recovery after the f270 ICFL failure near t=0.4035

The failed production still contains complete fields through `t=0.4`
(`lustre_2160.dat`).  The recovery launcher never repeats `t=0..0.4`.  It:

1. post-processes the nine saved original states through `t=0.4` to binary
   fields, audits density/pressure/velocity/CFL health, and renders cropped
   fixed-scale vorticity-shedding and shock-formation movies;
2. re-indexes the `t=0.4` checkpoint on a four-times-smaller time step and
   runs a short `t=0.4..0.5` stability gate;
3. submits the `t=0.5..3` continuation with an `afterok` dependency, so it can
   start only if both the source-field audit and the gate pass; and
4. after a successful continuation, combines all available times into final
   fixed-scale MP4s and a ZIP archive.  If a simulation stage fails, that
   stage post-processes its last complete checkpoint before exiting.

Run on a Unity login node:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/mfc-a40-iles-screen/mfc_iles_a40/unity_recover_iles.sh)
```

The source run defaults to
`.../runs/mfc_iles_a40/f270_t3_20260821-100843/f270`.  Override it only when
recovering an equivalent copied run by setting `SOURCE_CASE_DIR=/absolute/path`
before the one-line command.
