# DART pilot for matched Mach-3, alpha-40 CFD images

This folder is a diagnostic feasibility package for applying
[DART](https://github.com/mkturkcan/DART) to diamond-airfoil flow images. Its
primary inputs are the requested MFC Euler/slip-wall and MFC viscous/no-model
solutions at Mach 3 and 40 deg incidence. An SU2/SST image is retained only as
a secondary cross-solver control. The package includes clean detector inputs,
provenance, and a repeatable runner.

## Outcome recorded on 2026-08-29

The CFD assets passed the local integrity, image-readability, and matched-case
checks. DART commit `b4f954319ad4c26ab1372d130719eb2f4ddd4ea6` cloned and
installed, but CFD inference did **not** run on the available host:

1. the host had CPU-only PyTorch and no NVIDIA CUDA device;
2. DART's current CPU path contains CUDA-only constructor allocations;
3. the required `facebook/sam3` checkpoint is gated and returned HTTP 401
   without authenticated model access.

Therefore this branch contains no claimed DART boxes, masks, scores, or speed
measurements. See `results/dart_preflight_2026-08-29.json` and
`results/DART_ATTEMPT.md` for the fail-closed record.

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
and output paths in `results/inference/dart_run_report.json`.

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
token or checkpoint. The job writes a per-job environment record and creates
`research/dart_cfd_pilot/results/dart_unity-JOBID.tar.gz`.

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
