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
| `coarse` | 60 | 5c upstream/transverse | 5400 | Euler control and temporal statistics |
| `medium` | 120 | 5c upstream/transverse | 10800 | refinement after controls pass |

The coarse and medium defaults save 25 snapshots with the same physical
spacing (`Delta t=0.1875`). The box still does not reproduce the SU2 20-chord
farfield. Before using MFC loads in a publication, perform MFC-specific grid
and far-boundary studies.

The farfield boundaries are selected from the Cartesian-normal Mach number.
Supersonic inflow/outflow CBCs are used when the corresponding normal Mach
exceeds one; otherwise prescribed subsonic inflow and pressure-controlled
subsonic outflow CBCs are used. This replaces the earlier all-extrapolation
boundary setup.

## Unity: one-line submission

From a clone of this repository on Unity:

```bash
bash mfc_crosscheck/unity_submit_mfc.sh 30 euler medium
```

Arguments are `angle mode grid [steps] [save-every]`. Examples:

```bash
bash mfc_crosscheck/unity_submit_mfc.sh 20 euler coarse
bash mfc_crosscheck/unity_submit_mfc.sh 30 euler medium
bash mfc_crosscheck/unity_submit_mfc.sh 8 laminar coarse
```

The optional fourth and fifth arguments override the number of steps and the
snapshot interval. For example:

```bash
bash mfc_crosscheck/unity_submit_mfc.sh 30 euler medium 10800 450
```

The earlier 1800-step medium run advanced only `t=0.75`, or 2.25 chord
convection lengths. Its pressure and density fields changed by approximately
23% and 15% between the final saved fields, so it is a startup transient and
must not be used for validation.

The wrapper loads `apptainer/latest`, pulls the official MFC CPU container only
if it is missing, and submits `unity_mfc_cpu.sbatch`. The default container is:

```text
/project/pi_roohie_umass_edu/containers/mfc_latest_cpu.sif
```

Override the project or image location with `MFC_PROJECT_ROOT`,
`MFC_CONTAINER_DIR`, or `MFC_IMAGE`.

Each v2 run is archived separately under:

```text
mfc_runs/alpha<angle>_<mode>_<grid>_supbc_v2/
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

## Required control sequence

Do not begin with another medium run. Submit the two coarse Euler controls:

```bash
bash mfc_crosscheck/unity_submit_mfc_controls.sh
```

The `alpha=20 deg` case is the attached-shock control. The `alpha=30 deg`
case tests the high-incidence curved compression front and unsteady wake with
the corrected characteristic boundaries. Analyze both controls before
submitting the `alpha=30 deg` medium case.

## Publication-oriented analysis

The native MFC figures show the full computational box and do not mask the
immersed-boundary cells. For a close-up with the diamond geometry overlaid,
derived Mach and temperature fields, density-gradient magnitude, and a
saved-field stationarity check, run:

```bash
bash mfc_crosscheck/unity_analyze_mfc.sh \
  mfc_runs/alpha30_euler_coarse_supbc_v2/mfc_crosscheck-YYYYMMDD-HHMMSS
```

The analysis uses MFC's own Silo-HDF5 reader and writes the following under
the archive's `analysis/` directory:

- `mfc_fields_closeup.png`: pressure, density, Mach, temperature,
  density-gradient, and native Schlieren close-ups;
- `mfc_saved_field_change.png`: common-scale fields and differences between
  the last two available saved steps (selected automatically);
- `mfc_shock_ray.png` and `mfc_shock_ray.csv`: an upstream-ray diagnostic and,
  only when a nontrivial jump is resolved, estimated leading-edge stand-off;
- `mfc_shock_trace.png` and `mfc_shock_trace.csv`: two-dimensional tracking of
  the windward compression front, which a single upstream ray can miss;
- `mfc_mean_rms_fields.png`: temporal mean and RMS pressure, density, and Mach
  over the second half of the available snapshots;
- `mfc_shock_trace_statistics.png` and `.csv`: temporal mean and standard
  deviation of the two-dimensional compression-front trace;
- `mfc_load_history.png` and `.csv`: immersed-boundary `CD`, `CL`, and `CM`
  history when MFC's `ib_state_wrt` output is available;
- `mfc_metrics.json` and `mfc_validation_summary.txt`: numerical checks,
  including full-field, primary-wave, and wake relative L2 changes;
- `mfc_unsteady_metrics.json` and `mfc_unsteady_summary.txt`: temporal wave and
  load assessments.

The script labels this calculation explicitly as Euler (inviscid, slip wall).
The rolled-up structures downstream of the trailing edge are an inviscid
vortex/entropy-sheet instability regularized by the grid and numerical
dissipation; they are not a modeled turbulent boundary layer. Their existence
can be physically consistent, but their individual wavelength and amplitude
are grid dependent. Consequently, pointwise wake stationarity is not required.
Use temporal means/RMS and load histories, while checking primary-wave
stability separately.

If the upstream ray remains at freestream values, the report records
`NOT_DETECTED` instead of returning the ray's lower bound (`s/c=0.05`) as a
spurious stand-off distance. For the sharp leading edge, the two-dimensional
trace then distinguishes a curved front emanating from the tip from a genuinely
missing compression front. No MFC load or stand-off result is publication-ready
until temporal, coarse/medium, and far-boundary checks pass.
