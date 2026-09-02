#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4

set -Eeuo pipefail
: "${ANALYSIS_ROOT:?}"
: "${CASE_TABLE:?}"
: "${PYTHON_BIN:?}"
: "${RENDER_SCRIPT:?}"
: "${MFC_ROOT:?}"

output="$ANALYSIS_ROOT/visuals"
mkdir -p "$output" "$ANALYSIS_ROOT/matplotlib-cache/visuals"
failure="$output/VISUALIZATION_FAILED.txt"
trap 'rc=$?; trap - ERR; printf "status=FAILED\nexit_code=%s\n" "$rc" >"$failure"; exit "$rc"' ERR

export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-4}
export OPENBLAS_NUM_THREADS=${SLURM_CPUS_PER_TASK:-4}
export MPLCONFIGDIR="$ANALYSIS_ROOT/matplotlib-cache/visuals"

"$PYTHON_BIN" "$RENDER_SCRIPT" \
    --case-table "$CASE_TABLE" \
    --mfc-root "$MFC_ROOT" \
    --ml-dataset "$ANALYSIS_ROOT/ml_dataset" \
    --output "$output" \
    --fps 20 \
    2>&1 | tee "$output/render.log"

test -s "$output/VISUALIZATION_OK.txt"
test -s "$output/reynolds_final_schlieren.png"
test -s "$output/reynolds_final_vorticity.png"
test -s "$output/hll_t31_final_schlieren_vorticity.png"
test -s "$output/MFC_REYNOLDS_SCREENING_T00_T06_SCHLIEREN.mp4"
test -s "$output/MFC_REYNOLDS_SCREENING_T00_T06_VORTICITY.mp4"
test -s "$output/MFC_HLL_T26_T31_SCHLIEREN_VORTICITY.mp4"
test -s "$output/MFC_HLL_T00_T31_SCHLIEREN_VORTICITY.mp4"
rm -f "$failure"
echo "MFC_VISUALS_JOB=PASS"
