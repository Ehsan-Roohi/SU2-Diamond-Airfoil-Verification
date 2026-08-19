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
  executables and running jobs are not modified.

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
