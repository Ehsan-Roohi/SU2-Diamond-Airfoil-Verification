# SU2 Diamond-Airfoil Verification

This public repository accompanies Appendix 6A of the gas-dynamics book. It
contains four structured O-grids and twelve two-stage teaching cases for
**SU2 v8.5.0 (Harrier)**. The report-reproduction Euler sweep covers 0°, 1°,
2°, 3°, and 4°; the original Euler/laminar/SST teaching matrix retains 0°, 4°,
and 8°.

The repository teaches a reproducible sequence: define the geometry and
boundary conditions; initialize a robust first-order solution; restart with
second-order Monotonic Upstream-centered Schemes for Conservation Laws (MUSCL)
reconstruction of the mean-flow variables; then inspect residuals, force
histories, physicality warnings, symmetry, shock angle, and wall resolution
before accepting a result.

> **Scientific status.** The files are executable teaching configurations. The
> sharp-wall Euler cases from 0° through 4° are qualified report-reproduction
> references, not grid-converged benchmarks; the other seven cases remain
> unverified. The numerical
> checks in `run_case.py` prevent a normal SU2 exit from being mistaken for a
> converged run. However, `EXPECTED_RESULTS.csv` intentionally leaves CL/CD,
> shock-angle, symmetry, y+, runtime, RAM, and disk ranges as `TBD` where a clean
> case-specific reference run has not been established. Do not fill those cells
> from a figure or from a different mesh.

An independent v8.5.0 verification check found that earlier Roe-flux
`euler_alpha0` settings
did **not** satisfy their residual, nonphysical-state, or force-window criteria.
Controlled tuning showed that the Harten–Lax–van Leer–Contact (HLLC) flux at
CFL 0.1 for startup and 0.2 for the MUSCL
stage reduces that failure. A new sharp four-corner Euler mesh removes the
rounded-trailing-edge near-vacuum mechanism and produced zero physicality
warnings with stable symmetric loads. The distributed cases therefore use that sharp mesh
and the conservative HLLC settings for all Euler folders. The 0°–4° Euler
report sweep has archived force windows and zero physicality warnings. Alpha
zero additionally enforces symmetry and shock-angle checks. The plateaued
density residual remains an explicit qualified warning for all five cases. The
supporting evidence and rejected variants are recorded in `TUNING_REPORT.md`.

## Repository contents

| Path | Purpose |
|---|---|
| `cases/` | Euler cases at 0°–4° plus Euler, laminar, and SST cases at 0°, 4°, and 8° |
| `meshes/` | three rounded-corner grids and one sharp Euler grid |
| `geometry/` | nondimensional airfoil vertices and geometry notes |
| `scripts/run_case.py` | two-stage runner with fail-closed acceptance checks |
| `scripts/extract_wave_metrics.py` | native-grid shock-angle, symmetry, and y+ metrics |
| `scripts/generate_su2_report_assets.py` | regenerate Euler figures, tables, load sweep, SU2-only surrogate panels, and common-scale comparisons |
| release archive: `report_templates/` | SU2 laminar/SST comparison backgrounds; the script replaces their Euler column with the new native SU2 result |
| release archive: `reference_report_assets/` | reference copies of the figures produced for the revised report |
| `REPORT_REPRODUCTION_RESULTS.md` | archived five-case target values and the precise qualification status |
| `EXPECTED_RESULTS.csv` | explicit acceptance ranges and unresolved `TBD` entries |
| `TUNING_REPORT.md` | numerical choices, rejected variants, and limitations |
| `CHATGPT_CODEX_RUN_GUIDE.md` | optional guided workflow using Codex |
| `tests/` | wrapper, metric-extraction, and consistency tests |

## 1. Install SU2: beginner route

The easiest route is the official precompiled **OpenMP (OMP)** package. It does
not require a compiler or MPI.

- SU2 v8.5.0 release: <https://github.com/su2code/SU2/releases/tag/v8.5.0>
- Official installation page: <https://su2code.github.io/docs_v7/Installation/>
- Official quick start: <https://su2code.github.io/docs_v7/Quick-Start/>
- Optional installation video: <https://www.youtube.com/watch?v=gs8HNMtG8FM>
- Optional first-run video: <https://www.youtube.com/watch?v=ZK8_RxVKuUE>

The videos may show an older release. Use them only for visual orientation and
use the v8.5.0 commands and files in this package.

### Windows 10/11

1. Download the **Windows OMP** archive, not the source-code ZIP.
2. Extract it to a short path such as `C:\SU2\v8.5.0`.
3. Find the folder that contains `SU2_CFD.exe`.
4. Search the Start menu for **environment variables**. Open **Edit the
   environment variables for your account**, edit `Path`, choose **New**, and
   paste the folder from step 3.
5. Confirm the dialogs. Close all PowerShell windows and open a new one.
6. Check that Windows finds SU2:

```powershell
where.exe SU2_CFD
SU2_CFD --help
```

If `where.exe` finds nothing, the Path entry is wrong or PowerShell was not
reopened. Microsoft MPI is not needed for the OMP package.

The helper scripts require Python 3.10 or newer. Check `python --version` (or
`py --version` on Windows). If neither command works, install Python from
<https://www.python.org/downloads/> and select **Add python.exe to PATH** during
installation. SU2 itself can still be run with the two direct commands in
Section 4 if Python is unavailable.

### macOS or Linux

Extract the precompiled package, then point the shell to its `bin` folder:

```bash
export SU2_RUN=/absolute/path/to/SU2/bin
export PATH="$SU2_RUN:$PATH"
export PYTHONPATH="$SU2_RUN:$PYTHONPATH"
SU2_CFD --help
```

Add the three `export` lines to `~/.zshrc` (macOS default) or `~/.bashrc`
(common on Linux) only after the command works in the current terminal.

## 2. Download this repository

The complete student archive and revised Word appendix are published as assets
of release
[`v1.1.0-su2-only-report-reproduction`](https://github.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/releases/tag/v1.1.0-su2-only-report-reproduction).

Use GitHub's **Code → Download ZIP** button, or clone it with Git:

```bash
git clone https://github.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification.git
cd SU2-Diamond-Airfoil-Verification
```

The airfoil coordinates are documented in `geometry/diamond_vertices.csv`.
The sharp-grid generator is supplied; the rounded viscous grids are distributed
directly so students do not need a separate meshing program for the exercises.

## 3. First test: installation only

Extract the student-package ZIP to a writable folder such as Documents; do not
run it from inside the ZIP preview. Open PowerShell/Terminal in the extracted
`SU2-Diamond-Airfoil-Verification` folder and run the small first-order case:

```bash
python scripts/run_case.py smoke_test --smoke --threads 4
```

Its target is roughly 1–3 minutes on a recent laptop, but hardware varies. A
PASS means only that SU2 launched and wrote a nonempty history and restart file.
Nonphysical-point warnings are captured in the manifest but do not fail this
installation-only test. The 30-iteration smoke solution is deliberately
unconverged and must never be used for a force, wave-angle, or contour
comparison. This smoke policy is separate from the fail-closed production
policy used for every physical case.

## 4. Run a full teaching case

Each physical case has two stages:

1. `startup.cfg`: robust first-order flow initialization;
2. `second_order.cfg`: restart with second-order MUSCL reconstruction for the
   **mean-flow equations**.

The SST scalar transport remains first order (`MUSCL_TURB= NO`) in both stages.
This is intentional documentation of the distributed files, not a claim of
second-order turbulence transport.

Run both stages with the wrapper from the package root:

```bash
python scripts/run_case.py cases/euler_alpha0 --threads 4
```

The equivalent direct OpenMP commands, issued inside the case folder, are:

```bash
SU2_CFD -t 4 startup.cfg
SU2_CFD -t 4 second_order.cfg
```

Do **not** run `mpirun -np 4 SU2_CFD` with the OMP executable. That can launch
four independent solvers writing the same files. MPI is an advanced route that
requires an MPI-enabled SU2 build; only then use:

```bash
python scripts/run_case.py cases/euler_alpha0 --mpi 4
```

If old outputs exist, the wrapper stops instead of mixing runs. Preserve them
and continue with:

```bash
python scripts/run_case.py cases/euler_alpha0 --threads 4 --archive-old
```

## 5. What the wrapper actually checks

SU2 can exit with code zero before a case satisfies the book's numerical
acceptance criteria. Therefore `run_case.py` also:

- records the exact command, executable path, thread/rank count, UTC time, and
  each stage return code;
- tees solver output to timestamped logs;
- refuses stale output unless `--archive-old` is requested;
- requires both `history_*.csv` and ASCII `restart_*.csv` files;
- records the initial residual, final residual, and reduction in orders of
  magnitude. The default policy enforces both configured gates. A row may use
  the explicit `warning` policy only for a documented qualified teaching case;
  the manifest and terminal then say `QUALIFIED_PASS`, never a silent PASS;
- requires at least 200 readable CL and CD records for the final window; one or
  a few records can never be called stable;
- checks relative CL/CD peak-to-peak variation for nonzero incidence and uses
  an **absolute** CL peak-to-peak limit at zero incidence;
- detects the real SU2 spellings `nonphysical`, `non-physical`, and
  `non physical` for both points and reconstructed states (default limit zero);
- enforces CL/CD ranges whenever their cells are populated in
  `EXPECTED_RESULTS.csv`;
- automatically extracts and enforces populated native-grid symmetry and
  shock-angle-error limits from the newly written restart; it also enforces a
  populated maximum-y+ limit from a traceable `case_metrics.json` file;
- writes a machine-readable `logs/*_run_manifest.json`;
- returns exit code 2 when an acceptance check fails.

The qualified Euler alpha-zero row supplies a measured absolute CL stability
limit. The unverified laminar/SST alpha-zero rows deliberately leave
`cl_ptp_abs_limit=TBD`; they fail closed unless an instructor supplies a
traceable criterion, for example:

```bash
python scripts/run_case.py cases/laminar_alpha0 --threads 4 \
  --cl-absolute-tolerance INSTRUCTOR_VALUE
```

Do not replace `INSTRUCTOR_VALUE` with a number inferred from the failed run.
Most `residual_drop_min_orders` cells are also `TBD` pending clean case-specific
campaigns. Every production manifest records the measured initial value, final
value, and signed drop; a negative drop means the residual grew during the
assessed stage. The sharp Euler alpha-zero row intentionally treats its final
residual and negative post-switch drop as warnings while retaining strict
force, physicality, symmetry, and wave-angle gates.
In `EXPECTED_RESULTS.csv`, `yplus_target` is interpreted as an upper bound on
the measured maximum y+, and `symmetry_tolerance` is the normalized mirrored-
density RMS metric defined in Section 9 below.

A script PASS is a **numerical-check pass**, not proof of agreement with the
book. `QUALIFIED_PASS` means the populated teaching-reference checks passed but
listed residual warnings remain. Neither label implies grid independence.

To print final-window values again:

```bash
python scripts/summarize_history.py cases/euler_alpha0/history_second_order.csv --alpha 0
```

## 6. Output names

- `history_*.csv`: residual and integral-force history;
- `restart_*.csv`: ASCII restart state (`RESTART_ASCII` does not write `.dat`);
- `flow_*.vtu`: volume field for ParaView;
- `surface_flow_*.csv`: wall pressure, skin friction, and related quantities;
- `logs/*.log`: complete terminal output;
- `logs/*_run_manifest.json`: command and pass/fail record.

The valid SU2 history group is `AERO_COEFF_SURF`; the obsolete
`SURFACE_AERO_COEFF` spelling has been removed from all configurations.

## 7. Case map and permitted use

| Folder | Model | Angle of attack | Current permitted use |
|---|---|---:|---|
