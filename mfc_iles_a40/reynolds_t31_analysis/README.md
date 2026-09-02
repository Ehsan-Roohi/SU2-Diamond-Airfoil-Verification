# MFC Reynolds screening and HLL analysis through t=31

This workflow post-processes the completed Mach-3, alpha=40-degree MFC HLL
calculations without modifying any solver output. It also exports a
physics-labelled computer-vision training dataset. It compares:

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

The machine-vision dataset is available as an independent, first-priority
job. It has no dependency on force analysis, long-view article diagnostics,
movies, or aggregation:

```bash
bash mfc_iles_a40/reynolds_t31_analysis/unity_submit_cv_dataset.sh
```

This exports the four complete t=0..6 screening sequences plus the canonical
unique retained Re=1e6 fields through t=31. Check it with
`ANALYSIS_ROOT=/printed/path unity_check_cv_dataset.sh`; the finished training
root is `ANALYSIS_ROOT/ml_dataset`. If `/project` has less than 12 GB free, the
submitter automatically finds or allocates the 30-day Unity HPC Workspace
named `mfc-a40-cv` and writes there. Scratch has no snapshots, so copy or
archive the finished reproducible dataset before its printed expiration.

The complete article-analysis workflow remains below.

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
markers, exact field-series counts, final checkpoint sizes, reusable
diagnostics, and retained long-chain evidence before it calls `sbatch`. It then
submits a dependency chain:

1. Construct a symlink-only hybrid t=0..31 view. Dense diagnostics and movies
   are reused for deliberately pruned periods. Retained raw boundaries are
   hashed wherever both copies still exist, but byte identity is not assumed:
   MFC may rewrite the copied start-step output. The preceding stage's final
   checkpoint is canonical, and every handoff is checked against `stage.env`,
   its source/target directories, exact step range, and the next stage's PASS
   marker. Any nonidentical duplicate remains explicit in the final audit.
2. Analyze six case-table rows with at most two simultaneous array tasks. The
   original pruned Re=1e6 case uses its native IB load history plus its final
   raw field if a complete prior diagnostic package is unavailable.
3. Export all unique retained fields to fixed-grid training tensors, PNG
   inputs, shock masks/ridges, and Stage-8-compatible vortex targets.
4. Render common-scale final fields, the four-case screening movies, a new
   t=26..31 tail, and a normalized t=0..31 long movie joined to validated prior
   movie evidence.
5. Aggregate tables, plots, audit JSON, and a compact ZIP bundle.

All jobs use `--nice=5000`; dataset export and visualization run serially after
the analysis array, so the workflow avoids simultaneous high-volume reads
while the t=31..36 solver stage is active. Set `ARRAY_LIMIT=1` before
submission for the lowest possible filesystem load.

## Main products

The printed `ANALYSIS_ROOT` contains:

- `summary/reynolds_force_shock_summary.csv`: mean/RMS/95% CI lift and drag,
  force-source provenance, Strouhal screening, and bow-shock metrics.
- `summary/hll_t31_five_unit_windows.csv`: consecutive t=6..11 through
  t=26..31 stationarity windows. The intentionally sparse t=21..26 interval is
  never interpolated and receives no spectral claim.
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
- `visuals/MFC_REYNOLDS_SCREENING_T00_T06_SCHLIEREN.mp4` and
  `visuals/MFC_REYNOLDS_SCREENING_T00_T06_VORTICITY.mp4`: synchronized movies
  for the four complete screening sequences (Re=1e4 f180/f270, Re=5e4, and
  Re=1e5). The Re=1e6 history is not fabricated after raw-field pruning.
- `visuals/MFC_HLL_T26_T31_SCHLIEREN_VORTICITY.mp4`: movie rendered directly
  from the newly completed dense raw tail.
- `visuals/MFC_HLL_T00_T31_SCHLIEREN_VORTICITY.mp4`: synchronized long-HLL
  schlieren/vorticity movie, formed from validated t=0..26 movie evidence and
  the new raw t=26..31 tail.
- `ml_dataset/manifest.jsonl`: one provenance-rich record per unique retained
  CFD frame, including case, Re, grid, time, split, source path, and checksum.
- `ml_dataset/tensors/*.npz`: 512x512 training arrays with density, pressure,
  velocity, Mach, schlieren, signed vorticity, swirling strength, Q, Omega
  ratio, and Gamma2, plus body/fluid, shock, and signed-vortex targets.
- `ml_dataset/images/{schlieren,vorticity}/*.png`: fixed-scale image inputs for
  raster computer-vision pipelines.
- `ml_dataset/labels/`: lossless PNG shock masks/ridges, signed vortex
  heatmaps, per-frame vortex instances, exact body mask, and label-validity
  mask for frameworks that do not read NPZ targets directly.
- `ml_dataset/{vortex_catalogue,shock_catalogue,splits}.csv`: point/track and
  shock labels plus leakage-controlled temporal splits. `guard` frames must be
  excluded from model development.
- `ml_dataset/normalization.json`: per-channel mean/std/min/max computed only
  from `train` samples and label-valid fluid pixels, avoiding validation/test
  leakage.
- `ml_dataset/dataset_balance.csv`: per-case and per-split frame counts. The
  retained Re=1e6 fields are time-imbalanced relative to the low/intermediate
  Reynolds sequences, so matched-time subsets are required for causal
  Reynolds-number comparisons.
- `ml_dataset/catalogues/*_stage8_catalogue.csv`: per-case tables with the
  exact core columns consumed by the existing DART Stage-11 audit.
- `ml_dataset/DATASET_CARD.md`: schema, normalization, provenance, and label
  limitations. `ml_dataset/cv_dataset_loader.py` loads a selected split without
  additional project code. Vortex definitions match `research/dart_cfd_pilot`
  Stage 8/11.
- `summary/MFC_REYNOLDS_T31_AUDIT.json`: machine-readable results and caveats.
- `MFC_REYNOLDS_T31_ANALYSIS_CORE.zip`: compact diagnostics, dataset metadata,
  catalogues, audit products, and logs. The multi-gigabyte tensors/images stay
  in `ml_dataset/` and are intentionally not duplicated in this ZIP.

The MFC native immersed-boundary load history is used only when it is finite
and complete. Otherwise, the analyzer reconstructs pressure and viscous
surface traction from the primitive fields and labels those forces
provisional. A spectral peak is not called resolved unless at least five
cycles occur in the selected record and the analyzer's other quality gates
also pass. Shock/vortex training targets are physics-derived weak labels, not
hand-annotated ground truth, and must be manually audited before a publication
claim.

To inspect the newest workflow at any time, run:

```bash
bash mfc_iles_a40/reynolds_t31_analysis/unity_check_reynolds_t31_analysis.sh
```
