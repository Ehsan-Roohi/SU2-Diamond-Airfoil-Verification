#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT=${PROJECT_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}
if [[ -z "${ANALYSIS_ROOT:-}" ]]; then
    ANALYSIS_ROOT=$(find "$PROJECT_ROOT/analysis" -mindepth 1 -maxdepth 1 \
        -type d -name 'mfc_a40_cv_dataset_*' -printf '%T@ %p\n' 2>/dev/null | \
        sort -nr | head -n 1 | cut -d' ' -f2-)
fi
[[ -n "${ANALYSIS_ROOT:-}" && -d "$ANALYSIS_ROOT" ]] || {
    echo "ERROR: no CV-dataset output directory was found." >&2
    exit 2
}

job=
if [[ -f "$ANALYSIS_ROOT/SUBMISSION.env" ]]; then
    job=$(awk -F= '$1=="cv_dataset_job" {print $2}' "$ANALYSIS_ROOT/SUBMISSION.env")
fi
echo "ANALYSIS_ROOT=$ANALYSIS_ROOT"
echo "CV_DATASET_JOB=${job:-UNKNOWN}"
if [[ -n "$job" ]]; then
    echo "===== SQUEUE ====="
    squeue -j "$job" || true
    echo "===== SACCT ====="
    sacct -j "$job" -X --format=JobID%18,JobName%20,State%18,Elapsed,ExitCode,MaxRSS,NodeList%22 || true
fi
echo "===== PROGRESS ====="
manifest="$ANALYSIS_ROOT/ml_dataset/manifest.jsonl"
if [[ -f "$manifest" ]]; then
    echo "completed_manifest_frames=$(wc -l <"$manifest")"
else
    count=$(find "$ANALYSIS_ROOT/ml_dataset/tensors" -maxdepth 1 -type f \
        -name '*.npz' -printf '.' 2>/dev/null | wc -c)
    echo "completed_tensor_frames=$count"
fi
for name in DATASET_OK.txt DATASET_FAILED.txt; do
    path="$ANALYSIS_ROOT/ml_dataset/$name"
    if [[ -f "$path" ]]; then
        echo "===== $name ====="
        cat "$path"
    fi
done
if [[ -f "$ANALYSIS_ROOT/ml_dataset/DATASET_OK.txt" ]]; then
    echo "MFC_CV_DATASET_CHECK=PASS"
elif [[ -f "$ANALYSIS_ROOT/ml_dataset/DATASET_FAILED.txt" ]]; then
    echo "MFC_CV_DATASET_CHECK=FAILED"
    exit 1
else
    echo "MFC_CV_DATASET_CHECK=RUNNING_OR_PENDING"
fi
