# DART pilot for matched Mach-3, alpha-40 CFD images

This folder is a diagnostic feasibility package for applying
[DART](https://github.com/mkturkcan/DART) to diamond-airfoil flow images. Its
primary inputs are the requested MFC Euler/slip-wall and MFC viscous/no-model
solutions at Mach 3 and 40 deg incidence. An SU2/SST image is retained only as
a secondary cross-solver control. The package includes clean detector inputs,
provenance, and a repeatable runner.

## Unity outcome recorded on 2026-08-29

Unity job `63758578` completed the full CUDA pipeline on an NVIDIA A100-SXM4
80 GB GPU with PyTorch 2.7.0+cu126. All five repository preflight tests passed,
the gated `sam3.pt` checkpoint was verified, and DART returned code 0 for all
five CFD images. The result archive SHA-256 was
`802c97038cd54d1e888d8abc73d9a2d74e9336806dd2b41b6398718050f82145`.

The default natural-language prompts did not yield reliable CFD semantics.
Four images produced no detections at confidence 0.15. The Euler schlieren
image produced one large `separation bubble` mask with score 0.174, although
the Euler/slip-wall case does not support that physical interpretation. This is
therefore a low-confidence false positive, not a validated separation
diagnosis.

The completed run establishes that the DART/SAM3 pipeline is technically
reproducible on Unity, but the off-the-shelf model and prompts do not yet
provide trustworthy shock, wake, shear-layer, vortex, or separation labels for
these scientific contour images. Future claims require prompt/threshold
sensitivity tests and comparison with CFD-derived reference masks.

## Inputs

| Input | Physical model | Conditions | Recorded qualification |
|---|---|---|---|
| `inputs/euler_mfc/euler_mfc_alpha40_mach.png` | MFC Euler, inviscid slip-wall field | Mach 3, alpha 40 deg, t=13.5 | diagnostic field image |
| `inputs/euler_mfc/euler_mfc_alpha40_schlieren.png` | MFC Euler, inviscid slip-wall field | Mach 3, alpha 40 deg, t=13.5 | diagnostic field image |
| `inputs/viscous_mfc/mfc_viscous_alpha40_schlieren_t3.png` | MFC viscous/no-model, no-slip, 2-D ILES-like screen | Mach 3, alpha 40 deg, Re_c=1e6, t=3 | final field audit passed |
| `inputs/viscous_mfc/mfc_viscous_alpha40_vorticity_t3.png` | MFC viscous/no-model, no-slip, 2-D ILES-like screen | Mach 3, alpha 40 deg, Re_c=1e6, t=3 | final field audit passed |
| `inputs/viscous_su2_sst/su2_sst_alpha40_mach.png` | SU2 v8.5.0 compressible RANS, SST-1994m | Mach 3, alpha 40 deg, Re_c=1e6 | stable-load `QUALIFIED_PASS`; strict residual target not reached |

The complete MFC field montage, MFC grid/vortex comparison, MFC separation
diagnosis, and a four-field SU2/SST rendering are retained under `inputs/` as
provenance and visual-review evidence.

This is **not** an Euler-versus-viscous validation comparison. Even the two MFC
runs differ in wall condition, viscosity, grid, physical time, and numerical
setup. The SU2/SST control also differs in solver and turbulence closure.
Matching Mach number and incidence makes the images useful for a detector
feasibility check only.

## Intended DART test

Each case uses at most four prompts selected from `diamond airfoil`, `shock
wave`, `separation bubble`, `vortex`, `shear layer`, and `wake`; the exact list
is recorded in `dart_cases.json` and copied into each run record.

These prompts are intentionally ambitious. DART is trained from natural-image
semantics; a scalar contour, schlieren ridge, or recirculation zone is not a
conventional object. A useful result must therefore be reviewed visually and
against CFD-derived labels. Confidence alone is not physical validation.

## Reproduce the static checks

From the repository root:

```bash
python -m pip install pillow pytest
python research/dart_cfd_pilot/scripts/build_manifest.py
python -m pytest -q research/dart_cfd_pilot/tests
```

## Run DART after the external gates are satisfied

Use a CUDA host, clone DART at the recorded commit, accept the SAM3 model terms,
and provide the authorized checkpoint as a local path. Do not commit the model
weight or an access token to this public repository.

```bash
git clone https://github.com/mkturkcan/DART.git /path/to/DART
git -C /path/to/DART checkout b4f954319ad4c26ab1372d130719eb2f4ddd4ea6

python research/dart_cfd_pilot/scripts/run_dart_pilot.py \
  --dart-repo /path/to/DART \
  --checkpoint /secure/path/sam3.pt \
  --device cuda \
  --imgsz 1008 \
  --confidence 0.15
```

The default run requests masks as well as boxes. Add `--detection-only` for a
box-only timing screen. The runner records commands, return codes, log tails,
and output paths in `results/manual/dart_run_report.json`.

## Submit the pilot on Unity (A100)

The batch script creates a reusable Python 3.11 environment under the PI project
directory, installs the pinned DART commit and CUDA 12.6 PyTorch, runs the asset
tests, performs all five inference cases, and packages the results. It requests
one A100 GPU, 64 GB RAM, and two hours on the `gpu` partition.

First accept the model terms at
[facebook/sam3](https://huggingface.co/facebook/sam3). If `sam3.pt` is not
already present, load the Hugging Face token without putting it in shell history:

```bash
read -rsp "Hugging Face token: " HF_TOKEN; export HF_TOKEN; echo
```

After this branch is merged into `main`, submit from a Unity login node:

```bash
cd /project/pi_roohie_umass_edu/github_sync/SU2-Diamond-Airfoil-Verification && git switch main && git pull --ff-only && mkdir -p logs && sbatch --export=ALL research/dart_cfd_pilot/scripts/submit_unity_dart_pilot.sh
```

If the approved checkpoint already exists elsewhere, set
`SAM3_CHECKPOINT=/secure/path/sam3.pt` before submission. Do not commit the
token or checkpoint. Each job now uses the shallow result layout:

```text
research/dart_cfd_pilot/results/JOBID/
research/dart_cfd_pilot/results/JOBID.tar.gz
research/dart_cfd_pilot/results/JOBID.tar.gz.sha256.txt
```

## Stage 2: domain-transfer screen

The first Unity inference was technically successful but semantically weak:
four images had no detections and the only retained prediction was a
low-confidence false positive. Stage 2 tests whether this is mainly caused by
plot framing and prompt wording before any fine-tuning is attempted.

It performs three controlled changes:

1. removes titles, axes, color bars, and excess far field with normalized
   \`plot\`, \`body\`, and \`wake\` crops;
2. evaluates four synonyms within each relevant physical family while never
   asking an Euler case for a separation bubble;
3. records box scores down to 0.01 in detection-only mode, then reports counts
   at 0.01, 0.03, 0.05, 0.10, 0.15, and 0.30 without treating a low score as a
   physical detection.

The output contains the crops, bounded box previews, a compact prompt-score CSV,
and a JSON report. All files remain directly inside one per-job directory.
Stage 2 is still a diagnostic screen: physical acceptance requires comparison
with masks derived from CFD fields.

After the reusable Stage-1 environment exists, submit on Unity:

\`\`\`bash
cd /project/pi_roohie_umass_edu/github_sync/SU2-Diamond-Airfoil-Verification-dart
sbatch research/dart_cfd_pilot/scripts/submit_unity_dart_stage2.sh
\`\`\`

Expected layout:

\`\`\`text
research/dart_cfd_pilot/results/JOBID/
research/dart_cfd_pilot/results/JOBID.tar.gz
research/dart_cfd_pilot/results/JOBID.tar.gz.sha256.txt
\`\`\`

## Render the native SU2/SST field again

The source archive contained a 130,320-point, 129,600-quad VTU field. It is not
duplicated in this branch. With the original VTU available locally:

```bash
python -m pip install -r research/dart_cfd_pilot/requirements.txt
python research/dart_cfd_pilot/scripts/render_su2_vtu.py \
  /path/to/flow_second_order.vtu \
  research/dart_cfd_pilot/inputs/viscous_su2_sst/su2_sst_alpha40_fields.png
```

## Acceptance gate for a future inference run

A future DART result remains `diagnostic` until all of the following are
recorded:

- exact DART and checkpoint identities;
- GPU, PyTorch, resolution, threshold, and timing method;
- hand-reviewed false positives and false negatives for each prompt;
- comparison with geometry- or gradient-derived reference labels;
- sensitivity to prompt wording and confidence threshold;
- separate conclusions for airfoil detection and flow-structure segmentation.

## Stage 2 Unity result and Stage 3 decision gate

Unity job `63758937` completed Stage 2 on an NVIDIA A100-SXM4 80 GB GPU.
Its archive SHA-256 is
`0cc11d39cb3eae381dfe80977ae322690b66b108a9d70835e4bb3e5bcc33554d`.
Crops and prompt synonyms exposed a real but narrow domain-transfer signal:
the prompts `swirl`, `vortex`, and `spiral` repeatedly localized visible
vortex cores in the MFC vorticity field. The strongest `recirculation region`
prediction instead localized the solid diamond, and many other predictions
covered most of a crop. Stage 2 therefore supports temporal vortex screening,
not a general shock, wake, or separation detector.

Stage 3 tests whether the plausible single-frame vortex signal persists through
the existing 61-frame MFC movie from nondimensional time 0 to 3. It:

1. analyzes a fixed wake crop with four vortex prompt synonyms;
2. rejects boxes larger than 2% of the crop;
3. requires overlap from at least two distinct prompts;
4. requires overlap with a high-chroma vorticity-raster proxy;
5. runs ByteTrack and maps track centers back to the recorded physical plot
   coordinates;
6. reports trajectories, convection proxies, track-birth intervals, and a
   nondimensional shedding-frequency proxy.

The chroma gate is visualization-dependent and is not physical ground truth.
The Stage-3 claim gate can only justify building a raw-field benchmark. A paper
claim still requires labels computed from numerical vorticity, Rortex,
swirling strength, Q, or lambda2 and sensitivity to those definitions.

Submit from the repository root on Unity after checking out the exact merged
revision:

```bash
mkdir -p logs
sbatch research/dart_cfd_pilot/scripts/submit_unity_dart_stage3.sh
```

The original movie-products directory may be moved after a run is archived.
Stage 3 therefore accepts either the vorticity MP4 or the recorded
`mfc-iles-a40-initial-movie-products.zip`. It searches the configured Unity
data root for the exact basename and refuses ambiguous matches. An explicit
asset can be supplied without modifying the repository:

```bash
DART_STAGE3_VIDEO=/absolute/path/mfc-iles-a40-initial-vorticity-shedding.mp4 \
  sbatch --export=ALL research/dart_cfd_pilot/scripts/submit_unity_dart_stage3.sh

DART_STAGE3_ARCHIVE=/absolute/path/mfc-iles-a40-initial-movie-products.zip \
  sbatch --export=ALL research/dart_cfd_pilot/scripts/submit_unity_dart_stage3.sh
```

An archived video is extracted once under
`/project/pi_roohie_umass_edu/DART_CFD_PILOT/stage3-inputs`; it is not copied
into the per-job result directory.

The result remains shallow:

```text
research/dart_cfd_pilot/results/JOBID/
research/dart_cfd_pilot/results/JOBID.tar.gz
research/dart_cfd_pilot/results/JOBID.tar.gz.sha256.txt
```

Continue to raw-field validation only if `stage3_report.json` records
`claim_gate=temporal_signal_present_needs_raw_field_validation`. Otherwise,
the off-the-shelf DART route is stopped rather than tuned against one movie.

## Shock-Ridge-Aware CMCD solver-transfer audit

The current physics-first detector revision is documented in
[`SHOCK_RIDGE_AWARE_CMCD.md`](SHOCK_RIDGE_AWARE_CMCD.md).  It reuses the frozen
AA-ACB-CMCD candidate generator and adds closed-Q-island, multiradius velocity
winding, pressure-minimum, and thermodynamic shock-ridge vetoes.  The SU2
Mach-3, alpha-40 SST-URANS checkpoint is a development negative control, not
an independent validation case.  The runner differentiates on the native SU2
O-grid and produces physical field figures plus a per-candidate rejection
audit.  No SU2 rerun is required:

```bash
sbatch --export=ALL \
  research/dart_cfd_pilot/scripts/submit_unity_vortex_shock_ridge_aware.sh
```



## Stage 4: physics-gated track audit

Unity job `63761044` completed Stage 3 on 61 frames and produced 298
prompt-consensus detections, 34 tracked observations, and four initially
qualified track identities. A post-run audit showed that identities 26 and 32
overlap on the same physical structure (three shared frames, median physical
center distance 0.1002, median box IoU 0.3185). Only 11.4% of the accepted
consensus detections became track observations. Applying minimum observation,
lifetime, displacement, continuity, and score gates leaves two unique
provisional tracks (6 and 32), below the minimum of three.

Stage 4 makes this audit reproducible and prevents the Stage-3 inter-track-birth
frequency proxy from being used as a shedding frequency. It is CPU-only and
does not rerun DART. Without an independently generated raw-field reference
CSV, its strongest possible result is
`diagnostic_signal_present_raw_field_validation_required`.

Submit the audit for the recorded Stage-3 directory:

```bash
export DART_STAGE4_STAGE3_DIR=/project/pi_roohie_umass_edu/github_sync/SU2-Diamond-Airfoil-Verification-dart/research/dart_cfd_pilot/results/63761044
sbatch --export=ALL research/dart_cfd_pilot/scripts/submit_unity_dart_stage4.sh
```

For physical validation, provide a CSV with
`frame_index,reference_id,x_physical,y_physical` generated independently from
raw MFC fields using signed vorticity together with swirling strength or
Rortex:

```bash
export DART_STAGE4_REFERENCE_CSV=/absolute/path/raw_field_vortex_reference.csv
sbatch --export=ALL research/dart_cfd_pilot/scripts/submit_unity_dart_stage4.sh
```

The reference gate reports one-to-one precision, recall, F1, center RMSE, and
ID switches. A publication claim passes only if both temporal uniqueness and
physical-reference thresholds pass. Results retain the shallow layout:

```text
research/dart_cfd_pilot/results/JOBID/
research/dart_cfd_pilot/results/JOBID.tar.gz
research/dart_cfd_pilot/results/JOBID.tar.gz.sha256.txt
```


## Stage 5: raw-field reference and closed-loop validation

Stage 4 confirmed that the four Stage-3 identities include the duplicate pair
26/32 and reduce to two unique strictly qualified tracks. Job `63764699`
completed the audit in seven seconds, produced a checksum-valid 2 KB archive,
and correctly blocked both the movie-derived frequency proxy and publication
claim.

The original large MFC output directory is no longer present, but its exact
reproducibility sources remain on the historical branch
`agent/mfc-a40-iles-final-case` at commit
`6f71c45d1223dab62dc8f65b1f05dc369ab5932e`. Stage 5 regenerates the
recorded 61 raw states with MFC commit
`0c9a1d434410175ac483b8d71646455444e3b7eb`, without generating another
movie.

For each state it independently computes velocity-gradient vorticity and
two-dimensional swirling strength, checks the derived vorticity against MFC's
written `omega3`, and retains spatially separated centres that pass both the
`lambda_ci` and signed-vorticity gates. The centres are associated in time
only within a bounded displacement and matching rotation sign. Threshold
sensitivity is recorded at quantiles 0.985, 0.990, and 0.995.

The same batch job then reruns Stage 4 against
`stage5_reference.csv`, reporting one-to-one precision, recall, F1, centre
RMSE, and ID switches. A publication claim remains impossible unless both the
raw-reference gate and independent DART comparison pass.

Submit from the exact merged revision. The default work directory is
`/project/pi_roohie_umass_edu/DART_CFD_PILOT/stage5-mfc-raw`; it contains
large reproducible raw fields and is intentionally not placed inside the
GitHub result tree.

```bash
sbatch --export=ALL research/dart_cfd_pilot/scripts/submit_unity_dart_stage5_regenerate.sh
```

The small user-facing outputs remain shallow:

```text
research/dart_cfd_pilot/results/JOBID/
research/dart_cfd_pilot/results/JOBID.tar.gz
research/dart_cfd_pilot/results/JOBID.tar.gz.sha256.txt
```

## Stage 6: common-FOV sparse-localization audit

Unity job `63786255` reused the completed 61-state raw sequence and passed
the Stage-5 reference gates. Sixty of 61 frames had absolute agreement above
0.9 between MFC's written vorticity and independently differentiated velocity;
the excluded initial frame contained only machine-level values near
`1e-14`. The raw comparison associated 29 of 30 canonical DART observations
with a reference centre (precision 0.9667 and centre RMSE 0.1060), but raw
observation recall was only 0.0170.

Stage 6 tests whether that low recall is an artefact of unequal fields of view,
the invalid initial frame, or one arbitrary persistence definition. It:

1. maps the exact Stage-3 raster crop back to physical coordinates;
2. excludes raw-reference frames that fail the independent vorticity check;
3. recomputes reference tracks inside the common field of view;
4. reports inclusive, persistent, and strict track-definition sensitivity;
5. separates localization precision from track-identity and observation
   coverage; and
6. records a diagnostic sparse-localization claim without promoting it to a
   publication-level comprehensive detector claim.

Submit the lightweight audit without rerunning MFC or DART:

```bash
export DART_STAGE6_STAGE3_DIR=/project/pi_roohie_umass_edu/github_sync/SU2-Diamond-Airfoil-Verification-dart/research/dart_cfd_pilot/results/63761044
export DART_STAGE6_STAGE5_DIR=/project/pi_roohie_umass_edu/github_sync/SU2-Diamond-Airfoil-Verification-dart/research/dart_cfd_pilot/results/63786255
sbatch --export=ALL research/dart_cfd_pilot/scripts/submit_unity_dart_stage6.sh
```

The expected outcome is deliberately fail-closed: high precision with sparse
coverage is recorded as
`diagnostic_high_precision_sparse_vortex_localization`, not as validated
comprehensive vortex tracking.
