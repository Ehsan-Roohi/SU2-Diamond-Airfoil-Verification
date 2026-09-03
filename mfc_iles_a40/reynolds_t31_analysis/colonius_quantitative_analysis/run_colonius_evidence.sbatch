#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --job-name=mfc-tim-post

set -Eeuo pipefail
: "${DATASET:?complete ml_dataset path is required}"
: "${OUTPUT:?analysis output path is required}"
: "${ANALYZER:?analyzer path is required}"
: "${PYTHON_BIN:?python path is required}"

mkdir -p "$OUTPUT" "$OUTPUT/matplotlib-cache"
export MPLCONFIGDIR="$OUTPUT/matplotlib-cache"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"
export OPENBLAS_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"

failure="$OUTPUT/ANALYSIS_FAILED.txt"
trap 'rc=$?; trap - ERR; printf "status=FAILED\nexit_code=%s\n" "$rc" >"$failure"; exit "$rc"' ERR

"$PYTHON_BIN" "$ANALYZER" --dataset "$DATASET" --output "$OUTPUT" \
    2>&1 | tee "$OUTPUT/analysis.log"

test -s "$OUTPUT/TIM_COLONIUS_QUANTITATIVE_CHECKS.pdf"
grep -q '^status=PASS$' "$OUTPUT/ANALYSIS_OK.txt"
rm -f "$failure"
echo "TIM_COLONIUS_UNITY_JOB=PASS output=$OUTPUT"
