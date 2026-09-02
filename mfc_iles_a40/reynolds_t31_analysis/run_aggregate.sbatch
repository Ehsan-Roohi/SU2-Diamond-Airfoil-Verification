#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2

set -Eeuo pipefail
: "${ANALYSIS_ROOT:?}"
: "${CASE_TABLE:?}"
: "${PYTHON_BIN:?}"
: "${AGGREGATE_SCRIPT:?}"

mkdir -p "$ANALYSIS_ROOT/summary" "$ANALYSIS_ROOT/matplotlib-cache/aggregate"
failure="$ANALYSIS_ROOT/summary/AGGREGATE_FAILED.txt"
trap 'rc=$?; trap - ERR; printf "status=FAILED\nexit_code=%s\n" "$rc" >"$failure"; exit "$rc"' ERR

export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-2}
export OPENBLAS_NUM_THREADS=${SLURM_CPUS_PER_TASK:-2}
export MPLCONFIGDIR="$ANALYSIS_ROOT/matplotlib-cache/aggregate"

"$PYTHON_BIN" "$AGGREGATE_SCRIPT" \
    --analysis-root "$ANALYSIS_ROOT" \
    --case-table "$CASE_TABLE" \
    2>&1 | tee "$ANALYSIS_ROOT/summary/aggregate.log"

test -s "$ANALYSIS_ROOT/ANALYSIS_COMPLETE.txt"
test -s "$ANALYSIS_ROOT/MFC_REYNOLDS_T31_ANALYSIS_CORE.zip"
test -s "$ANALYSIS_ROOT/summary/MFC_REYNOLDS_T31_AUDIT.json"
test -s "$ANALYSIS_ROOT/summary/MFC_REYNOLDS_T31_SUMMARY.md"
test -s "$ANALYSIS_ROOT/ml_dataset/DATASET_OK.txt"
grep -qx 'status=PASS' "$ANALYSIS_ROOT/ml_dataset/DATASET_OK.txt"
rm -f "$failure"
echo "MFC_AGGREGATE_JOB=PASS"
