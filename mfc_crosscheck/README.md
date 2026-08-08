# MFC cross-check for the Mach-3 diamond airfoil

This directory provides an independent MFC calculation corresponding to the
sharp-Euler geometry and freestream used by this SU2 verification repository:

- symmetric diamond, `c=1`, half-angle `8 deg`, `t/c=0.1405408348`;
- ideal gas, `gamma=1.4`, `M_infinity=3`;
- selectable angle of attack;
- Euler slip wall, or a diagnostic constant-viscosity laminar mode at
  `Re_c=1e6`;
- HLLC flux, fifth-order WENO reconstruction, RK3 time integration, and an
  STL ghost-cell immersed boundary.

The case was developed against MFC commit
`0c9a1d434410175ac483b8d71646455444e3b7eb`.

## Scientific scope

MFC does not currently implement RANS or SST k-omega. Consequently, this case
cannot reproduce the SU2 `SST_OPTIONS=V1994m` solutions. The MFC laminar option
also retains the sharp STL and constant nondimensional viscosity, whereas the
retained SU2 viscous cases use a rounded `r_corner/c=0.001` body-fitted O-grid
and the Sutherland law.

The useful comparison is therefore:

1. SU2 Euler versus MFC Euler for wave topology, shock angle, and surface-field
   trends;
2. detached-shock stand-off and density-gradient topology at `alpha=30 deg`;
3. a secondary qualitative laminar diagnostic, not an SST replacement.

At `alpha=20 deg`, the windward turn is `28 deg`, below the Mach-3 maximum
attached turn of approximately `34.07 deg`. At `alpha=30 deg`, the windward
turn is `38 deg`, so its leading-edge shock must detach.

## Case presets

| Preset | Approx. cells/chord | Box | Default steps | Purpose |
|---|---:|---|---:|---|
| `smoke` | 20 | compact | 20 | build and runtime check |
| `coarse` | 60 | 5c upstream/transverse | 900 | first physical diagnostic |
| `medium` | 120 | 5c upstream/transverse | 1800 | refined diagnostic |

The box still does not reproduce the SU2 20-chord farfield. Before using MFC
loads in a publication, perform MFC-specific grid and far-boundary studies.

## Unity: one-line submission

From a clone of this repository on Unity:

```bash
bash mfc_crosscheck/unity_submit_mfc.sh 30 euler medium
```

Arguments are `angle mode grid`. Examples:

```bash
bash mfc_crosscheck/unity_submit_mfc.sh 20 euler coarse
bash mfc_crosscheck/unity_submit_mfc.sh 30 euler medium
bash mfc_crosscheck/unity_submit_mfc.sh 8 laminar coarse
```

The wrapper loads `apptainer/latest`, pulls the official MFC CPU container only
if it is missing, and submits `unity_mfc_cpu.sbatch`. The default container is:

```text
/project/pi_roohie_umass_edu/containers/mfc_latest_cpu.sif
```

Override the project or image location with `MFC_PROJECT_ROOT`,
`MFC_CONTAINER_DIR`, or `MFC_IMAGE`.

Each completed run is archived separately under:

```text
mfc_runs/alpha<angle>_<mode>_<grid>/
```

The Slurm log records the container SHA-256 so the exact runtime image can be
identified even though the initial pull uses the upstream `latest-cpu` tag.

## Local MFC usage

With a native MFC checkout:

```bash
./mfc.sh run /path/to/SU2-Diamond-Airfoil-Verification/mfc_crosscheck/case.py \
  -n 8 -- \
  --alpha 30 --mode euler --grid medium
```

The Silo output includes pressure, density, velocity components, and a
Schlieren field. Compare integrated loads only after convergence and
far-boundary sensitivity have been established.

## Publication-oriented analysis

The native MFC figures show the full computational box and do not mask the
immersed-boundary cells. For a close-up with the diamond geometry overlaid,
derived Mach and temperature fields, density-gradient magnitude, and a
saved-field stationarity check, run:

```bash
bash mfc_crosscheck/unity_analyze_mfc.sh \
  mfc_runs/alpha30_euler_medium/mfc_crosscheck-YYYYMMDD-HHMMSS
```

The analysis uses MFC's own Silo-HDF5 reader and writes the following under
the archive's `analysis/` directory:

- `mfc_fields_closeup.png`: pressure, density, Mach, temperature,
  density-gradient, and native Schlieren close-ups;
- `mfc_saved_field_change.png`: common-scale fields and differences between
  steps 1500 and 1800;
- `mfc_shock_ray.png` and `mfc_shock_ray.csv`: an upstream-ray diagnostic and
  estimated leading-edge shock stand-off;
- `mfc_metrics.json` and `mfc_validation_summary.txt`: numerical checks,
  including near-body relative L2 changes and freestream preservation.

The script labels this calculation explicitly as Euler (inviscid, slip wall).
Visible wake-like spots or Cartesian-grid ripples are therefore numerical or
transient structures, not evidence of modeled turbulence. Do not use MFC
loads or stand-off values in a publication until the stationarity, grid, and
far-boundary checks all pass.
