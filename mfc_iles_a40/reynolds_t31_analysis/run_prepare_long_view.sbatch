#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1

set -Eeuo pipefail
: "${ANALYSIS_ROOT:?}"
: "${PYTHON_BIN:?}"
: "${BUILD_SCRIPT:?}"
: "${RE1E6_INITIAL:?}"
: "${LONG_CHAIN:?}"

failure="$ANALYSIS_ROOT/PREPARE_FAILED.txt"
trap 'rc=$?; trap - ERR; printf "status=FAILED\nexit_code=%s\n" "$rc" >"$failure"; exit "$rc"' ERR

export OMP_NUM_THREADS=1
export MPLCONFIGDIR="$ANALYSIS_ROOT/matplotlib-cache"
mkdir -p "$MPLCONFIGDIR"

"$PYTHON_BIN" "$BUILD_SCRIPT" \
    --initial "$RE1E6_INITIAL" \
    --chain "$LONG_CHAIN" \
    --output "$ANALYSIS_ROOT/long_view" \
    --start-time 0 \
    --end-time 31 \
    2>&1 | tee "$ANALYSIS_ROOT/prepare-long-view.log"

test -s "$ANALYSIS_ROOT/long_view/LONG_VIEW_OK.txt"
test -s "$ANALYSIS_ROOT/long_view/long_view_manifest.json"
rm -f "$failure"
echo "MFC_LONG_VIEW_JOB=PASS"
