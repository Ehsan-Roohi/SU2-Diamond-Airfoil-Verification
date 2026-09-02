#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4

set -Eeuo pipefail
: "${ANALYSIS_ROOT:?}"
: "${CASE_TABLE:?}"
: "${PYTHON_BIN:?}"
: "${ANALYZER:?}"
: "${CASE_EVIDENCE_SCRIPT:?}"
: "${PRUNED_ANALYZER:?}"
: "${LONG_ANALYZER:?}"
: "${MFC_ROOT:?}"
: "${SLURM_ARRAY_TASK_ID:?}"

row_number=$((SLURM_ARRAY_TASK_ID + 2))
row=$(sed -n "${row_number}p" "$CASE_TABLE")
[[ -n "$row" ]] || { echo "ERROR: no case-table row for array index $SLURM_ARRAY_TASK_ID" >&2; exit 2; }
IFS=$'\t' read -r label display reynolds grid dt case_dir analysis_start role <<<"$row"
for value in "$label" "$display" "$reynolds" "$grid" "$dt" "$case_dir" "$analysis_start" "$role"; do
    [[ -n "$value" ]] || { echo "ERROR: incomplete case-table row $row_number" >&2; exit 2; }
done

output="$ANALYSIS_ROOT/cases/$label"
mkdir -p "$output" "$ANALYSIS_ROOT/matplotlib-cache/$label"
failure="$output/ANALYSIS_FAILED.txt"
trap 'rc=$?; trap - ERR; printf "status=FAILED\ncase=%s\nexit_code=%s\n" "$label" "$rc" >"$failure"; exit "$rc"' ERR

export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-4}
export OPENBLAS_NUM_THREADS=${SLURM_CPUS_PER_TASK:-4}
export MPLCONFIGDIR="$ANALYSIS_ROOT/matplotlib-cache/$label"

if [[ "$role" == long_baseline ]]; then
    "$PYTHON_BIN" "$LONG_ANALYZER" "$case_dir" \
        --mfc-root "$MFC_ROOT" \
        --output-dir "$output" \
        --dt "$dt" \
        --analysis-start "$analysis_start" \
        --alpha 40 \
        --rho-inf 1 \
        --u-inf 3 \
        --chord 1 \
        --reynolds "$reynolds" \
        2>&1 | tee "$output/analyze.log"
else
    "$PYTHON_BIN" "$CASE_EVIDENCE_SCRIPT" "$case_dir" \
        --dt "$dt" \
        --analysis-start "$analysis_start" \
        --analyzer "$ANALYZER" \
        --pruned-analyzer "$PRUNED_ANALYZER" \
        --mfc-root "$MFC_ROOT" \
        --output-dir "$output" \
        --alpha 40 \
        --rho-inf 1 \
        --u-inf 3 \
        --chord 1 \
        --reynolds "$reynolds"
fi

test -s "$output/mfc_hll_article_metrics.json"
test -s "$output/mfc_hll_force_history.csv"
test -s "$output/mfc_hll_shock_history.csv"
"$PYTHON_BIN" - "$output/mfc_hll_article_metrics.json" "$label" <<'PY'
import json
import math
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
for coefficient in ("CL", "CD"):
    metrics = payload["force_statistics"][coefficient]
    assert metrics["samples"] >= 16
    assert all(math.isfinite(float(metrics[key])) for key in ("mean", "rms_fluctuation", "ci95_mean"))
assert payload["saved_steps"] == sorted(set(payload["saved_steps"]))
print(f"CASE_ANALYSIS_GATE=PASS case={sys.argv[2]}")
PY

printf 'status=PASS\ncase=%s\nrole=%s\ngrid=%s\nRe_c=%s\n' \
    "$label" "$role" "$grid" "$reynolds" >"$output/ANALYSIS_OK.txt"
rm -f "$failure"
echo "MFC_CASE_ANALYSIS=PASS case=$label"
