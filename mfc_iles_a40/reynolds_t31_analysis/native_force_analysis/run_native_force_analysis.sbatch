#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=6G
#SBATCH --time=01:00:00
#SBATCH --job-name=mfc-native-force

set -Eeuo pipefail

: "${ANALYZER:?native-force analyzer path is required}"
: "${PYTHON_BIN:?python path is required}"
: "${OUTPUT:?analysis output path is required}"
: "${RE1E4_ROOT:?Re=1e4 root is required}"
: "${LADDER_ROOT:?Re=5e4/Re=1e5 ladder root is required}"
: "${RE1E6_INITIAL:?Re=1e6 initial root is required}"
: "${LONG_CHAIN:?Re=1e6 long-chain root is required}"

mkdir -p "$OUTPUT" "$OUTPUT/matplotlib-cache"
export MPLCONFIGDIR="$OUTPUT/matplotlib-cache"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-2}"
export OPENBLAS_NUM_THREADS="${SLURM_CPUS_PER_TASK:-2}"

failure="$OUTPUT/ANALYSIS_FAILED.txt"
trap 'rc=$?; trap - ERR; printf "status=FAILED\nexit_code=%s\n" "$rc" >"$failure"; exit "$rc"' ERR

archive="$(dirname -- "$OUTPUT")/TIM_COLONIUS_NATIVE_FORCES_JOB${SLURM_JOB_ID}.zip"

"$PYTHON_BIN" "$ANALYZER" \
    --re1e4-root "$RE1E4_ROOT" \
    --ladder-root "$LADDER_ROOT" \
    --re1e6-initial "$RE1E6_INITIAL" \
    --long-chain "$LONG_CHAIN" \
    --output "$OUTPUT" \
    --archive "$archive" \
    2>&1 | tee "$OUTPUT/analysis.log"

test -s "$OUTPUT/ANALYSIS_COMPLETE.txt"
grep -Eq '^status=(PASS|PARTIAL)$' "$OUTPUT/ANALYSIS_COMPLETE.txt"
test -s "$OUTPUT/TIM_COLONIUS_REYNOLDS_FORCES.png"
test -s "$OUTPUT/TIM_COLONIUS_NATIVE_FORCES.pdf"
test -s "$archive"
sha256sum "$archive" >"$archive.sha256.txt"
printf '%s\n' "$archive" >"$OUTPUT/ARCHIVE_PATH.txt"
rm -f "$failure"

printf 'MFC_NATIVE_FORCE_JOB=COMPLETE\nOUTPUT=%s\nARCHIVE=%s\n' "$OUTPUT" "$archive"
