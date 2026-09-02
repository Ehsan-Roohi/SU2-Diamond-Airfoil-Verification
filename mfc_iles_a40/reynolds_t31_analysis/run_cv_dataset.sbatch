#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4

set -Eeuo pipefail
: "${ANALYSIS_ROOT:?}"
: "${CASE_TABLE:?}"
: "${PYTHON_BIN:?}"
: "${CV_EXPORT_SCRIPT:?}"
: "${MFC_ROOT:?}"

output="$ANALYSIS_ROOT/ml_dataset"
mkdir -p "$output" "$ANALYSIS_ROOT/matplotlib-cache/ml-dataset"
failure="$output/DATASET_FAILED.txt"
trap 'rc=$?; trap - ERR; printf "status=FAILED\nexit_code=%s\n" "$rc" >"$failure"; exit "$rc"' ERR

export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-4}
export OPENBLAS_NUM_THREADS=${SLURM_CPUS_PER_TASK:-4}
export MPLCONFIGDIR="$ANALYSIS_ROOT/matplotlib-cache/ml-dataset"

"$PYTHON_BIN" "$CV_EXPORT_SCRIPT" \
    --case-table "$CASE_TABLE" \
    --mfc-root "$MFC_ROOT" \
    --output "$output" \
    --width 512 \
    --height 512 \
    2>&1 | tee "$output/export.log"

test -s "$output/DATASET_OK.txt"
grep -qx 'status=PASS' "$output/DATASET_OK.txt"
test -s "$output/manifest.jsonl"
test -s "$output/vortex_catalogue.csv"
test -s "$output/shock_catalogue.csv"
test -s "$output/dataset_report.json"
test -s "$output/normalization.json"
test -s "$output/dataset_balance.csv"
test -s "$output/labels/body_mask.png"
test -s "$output/labels/label_valid_mask.png"
test -n "$(find "$output/labels/shock_mask" -maxdepth 1 -type f -name '*.png' -print -quit)"
test -n "$(find "$output/labels/vortex_instances" -maxdepth 1 -type f -name '*.png' -print -quit)"
rm -f "$failure"
echo "MFC_CV_DATASET_JOB=PASS"
