# Tim Colonius quantitative Reynolds checks

This bundle performs **post-processing only** on the completed full Unity
`ml_dataset`. It does not submit or continue an MFC simulation.

It produces three concise figure pages:

1. same-grid (`f180`) Reynolds histories for `Re=1e4, 5e4, 1e5`;
2. scale-separated `f180`/`f270` sensitivity at `Re=1e4`;
3. drift histories on the final continuous retained `Re=1e6` segment.

The analysis intentionally does not infer lift or drag from the resampled
training tensors. CL/CD require the raw/native immersed-boundary force path and
an independent force-source cross-check.

Instantaneous f180/f270 correlations are phase-sensitive. Interpret them with
the time histories and small-scale energy ratios, not as a standalone
convergence test.

## Unity command

```bash
DATASET=/scratch4/workspace/roohie_umass_edu-mfc-a40-cv/mfc_a40_cv_dataset_20260902-233057/ml_dataset
bash unity_submit_colonius_evidence.sh "$DATASET"
```

The submitter creates a timestamped output directory and prints `JOB_ID`,
`OUTPUT`, the checker command, and the final PDF path. Run the exact `CHECK=`
command it prints, or use:

```bash
bash unity_check_colonius_evidence.sh OUTPUT
```

Send Tim only `TIM_COLONIUS_QUANTITATIVE_CHECKS.pdf` together with the existing
three-page field-comparison PDF. Keep the CSV/JSON files for audit; do not send
the ML dataset unless he explicitly requests it.
