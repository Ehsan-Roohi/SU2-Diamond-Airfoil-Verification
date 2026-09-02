# MFC Reynolds screening and HLL analysis through t=31

This workflow post-processes the completed Mach-3, alpha=40-degree MFC HLL
calculations without modifying any solver output. It compares:

| Label | Reynolds number | Grid | Time range | Purpose |
|---|---:|---:|---:|---|
| `re1e4_f180` | 10,000 | f180 | 0..6 | grid control |
| `re1e4_f270` | 10,000 | f270 | 0..6 | primary low-Re case |
| `re5e4_f180` | 50,000 | f180 | 0..6 | screening control |
| `re1e5_f180` | 100,000 | f180 | 0..6 | screening control |
| `re1e6_f270` | 1,000,000 | f270 | 0..6 | primary high-Re case |
| `re1e6_long_t31` | 1,000,000 | f270 | 0..31 | stationarity/restart audit |

The two intermediate-Re f180 cases are screening evidence only. They must not
be described as grid-converged results. The intended decision is to repeat on
f270 only the first intermediate Reynolds number at which fine-scale wake
structure clearly reappears.

## Unity submission

Movie rendering needs an ffmpeg executable. The preflight accepts, in order,
`FFMPEG_BIN=/absolute/path/to/ffmpeg`, an `ffmpeg` executable on `PATH`,
or the binary bundled by the `imageio-ffmpeg` Python package. If Unity does
not provide an ffmpeg module, install the small Python fallback for the same
interpreter selected by `PYTHON_BIN`:

```bash
"${PYTHON_BIN:-$(command -v python3)}" -m pip install --user imageio-ffmpeg
```

From the pinned workflow branch, run:

```bash
bash mfc_iles_a40/reynolds_t31_analysis/unity_submit_reynolds_t31_analysis.sh
```

The submitter discovers the newest *completed* inputs, checks their PASS
markers and final checkpoint sizes, runs the analyzer self-test, and submits a
dependency chain:

1. Construct a symlink-only t=0..31 view, validate every source boundary
   against the next stage's PASS marker, and hash every retained duplicate.
2. Analyze six case-table rows with at most two simultaneous array tasks.
3. Render common-scale final fields and three MP4 movies.
4. Aggregate tables, plots, audit JSON, and a compact ZIP bundle.

All jobs use `--nice=5000`; the visualization waits for the analysis array, so
the workflow avoids simultaneous high-volume reads while the t=31..36 solver
stage is active. Set `ARRAY_LIMIT=1` before submission for the lowest possible
filesystem load.

## Main products

The printed `ANALYSIS_ROOT` contains:

- `summary/reynolds_force_shock_summary.csv`: mean/RMS/95% CI lift and drag,
  force-source provenance, Strouhal screening, and bow-shock metrics.
- `summary/hll_t31_five_unit_windows.csv`: consecutive t=6..11 through
  t=26..31 stationarity windows.
- `summary/hll_t31_restart_continuity.csv`: force-increment checks around every
  restart boundary; `long_view/long_view_manifest.json` records source hashes,
  restart-marker hashes, and byte comparisons wherever the copied boundary was
  retained by MFC.
- `summary/*.png`: matched force histories, pressure/viscous force components,
  lift spectra, shock histories, Reynolds trends, and long-time stationarity.
- `summary/reynolds_neighbor_relative_changes.csv`: adjacent-Re screening
  changes, with every grid transition recorded explicitly.
- `visuals/reynolds_final_*.png`: fixed-view, fixed-color-scale schlieren,
  vorticity, Mach, and pressure-coefficient comparisons at t=6.
- `visuals/re1e4_f180_f270_grid_check.png`: the explicit low-Re grid check.
- `visuals/hll_t31_final_schlieren_vorticity.png`: the long-run final state.
- `visuals/MFC_REYNOLDS_T00_T06_SCHLIEREN.mp4` and
  `visuals/MFC_REYNOLDS_T00_T06_VORTICITY.mp4`: four-Re synchronized movies.
- `visuals/MFC_HLL_T00_T31_SCHLIEREN_VORTICITY.mp4`: synchronized long-HLL
  schlieren/vorticity movie.
- `summary/MFC_REYNOLDS_T31_AUDIT.json`: machine-readable results and caveats.
- `MFC_REYNOLDS_T31_ANALYSIS_CORE.zip`: all non-movie products and logs.

The MFC native immersed-boundary load history is used only when it is finite
and complete. Otherwise, the existing analyzer reconstructs pressure and
viscous surface traction from the primitive fields and labels those forces
provisional. A spectral peak is not called resolved unless at least five
cycles occur in the selected record and the analyzer's other quality gates
also pass.

To inspect the newest workflow at any time, run:

```bash
bash mfc_iles_a40/reynolds_t31_analysis/unity_check_reynolds_t31_analysis.sh
```
