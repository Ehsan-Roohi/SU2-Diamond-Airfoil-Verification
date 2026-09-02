#!/usr/bin/env bash
set -Eeuo pipefail
trap 'rc=$?; trap - ERR; echo "ERROR: CV-dataset submitter stopped at line $LINENO (exit $rc)." >&2; exit "$rc"' ERR

PROJECT_ROOT=${PROJECT_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}
REPO_ROOT=${REPO_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification}
MFC_ROOT=${MFC_ROOT:-$REPO_ROOT/third_party/MFC-0c9a1d43-iles-portable-v3}
OUTPUT_PARENT=${OUTPUT_PARENT:-$PROJECT_ROOT/analysis}
MAIL_USER=${MAIL_USER:-roohie@umass.edu}
SLURM_USER=${SLURM_USER:-${USER:-$(id -un)}}
PYTHON_BIN=${PYTHON_BIN:-$(command -v python3)}
CONSTRAINT=${CONSTRAINT:-intel&x86_64_v4}
CV_MEMORY=${CV_MEMORY:-48G}
CV_WALLTIME=${CV_WALLTIME:-24:00:00}

BASE=$REPO_ROOT/mfc_iles_a40/reynolds_t31_analysis
VIEW_BUILDER=$BASE/build_cv_raw_view.py
RAW_RESTART_READER=$BASE/raw_restart_reader.py
CV_EXPORT_SCRIPT=$BASE/export_cv_dataset.py
CV_LABEL_SCRIPT=$BASE/cv_physics_labels.py
CV_LOADER=$BASE/cv_dataset_loader.py
CV_RUNNER=$BASE/run_cv_dataset.sbatch

latest_complete() {
    local root=$1
    local pattern=$2
    shift 2
    local -a candidates=()
    local entry candidate marker ok
    [[ -d "$root" ]] || return 1
    mapfile -t candidates < <(
        find "$root" -mindepth 1 -maxdepth 1 -type d -name "$pattern" \
            -printf '%T@ %p\n' 2>/dev/null | sort -nr
    )
    for entry in "${candidates[@]}"; do
        candidate=${entry#* }
        ok=1
        for marker in "$@"; do
            [[ -f "$candidate/$marker" ]] || { ok=0; break; }
        done
        if ((ok)); then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

RE1E4_ROOT=${RE1E4_ROOT:-}
if [[ -z "$RE1E4_ROOT" ]]; then
    RE1E4_ROOT=$(latest_complete \
        "$PROJECT_ROOT/runs/mfc_iles_a40_tim_high_viscosity" \
        're1e4_t000_t060_*' \
        f180/RUN_OK_HIGH_VISCOSITY.txt \
        f270/RUN_OK_HIGH_VISCOSITY.txt) || {
        echo "ERROR: no completed Re=1e4 f180/f270 source was found." >&2
        exit 2
    }
fi

LADDER_ROOT=${LADDER_ROOT:-}
if [[ -z "$LADDER_ROOT" ]]; then
    LADDER_ROOT=$(latest_complete \
        "$PROJECT_ROOT/runs/mfc_iles_a40_tim_viscosity_ladder" \
        'f180_re5e4_re1e5_t000_t060_*' \
        re5e4/RUN_OK_VISCOSITY_LADDER.txt \
        re1e5/RUN_OK_VISCOSITY_LADDER.txt) || {
        echo "ERROR: no completed Re=5e4/Re=1e5 source was found." >&2
        exit 2
    }
fi

RE1E6_INITIAL=${RE1E6_INITIAL:-}
if [[ -z "$RE1E6_INITIAL" ]]; then
    RE1E6_INITIAL=$(latest_complete \
        "$PROJECT_ROOT/runs/mfc_iles_a40_fresh_hll_production" \
        'f270_t000_t0600_w5unmapped_hll_dt1_*' RUN_OK_INITIAL.txt) || {
        echo "ERROR: no completed Re=1e6 initial source was found." >&2
        exit 2
    }
fi

LONG_CHAIN=${LONG_CHAIN:-}
if [[ -z "$LONG_CHAIN" ]]; then
    LONG_CHAIN=$(latest_complete \
        "$PROJECT_ROOT/runs/mfc_iles_a40_hll_long_baseline" \
        'f270_t060_t360_hll_dt1_*' \
        t06_t11/RUN_OK_RESTART.txt \
        t11_t16/RUN_OK_RESTART.txt \
        t16_t21/RUN_OK_RESTART.txt \
        t21_t26/RUN_OK_RESTART.txt \
        t26_t31/RUN_OK_RESTART.txt) || {
        echo "ERROR: no completed Re=1e6 restart chain through t=31 was found." >&2
        exit 2
    }
fi

require_series() {
    local directory=$1 expected_count=$2 bytes=$3 first=$4 last=$5 stride=$6
    local step field count
    for ((step=first; step<=last; step+=stride)); do
        field="$directory/restart_data/lustre_${step}.dat"
        [[ -f "$field" && "$(stat -c %s "$field")" -eq "$bytes" ]] || {
            echo "ERROR: missing or truncated training frame: $field" >&2
            exit 3
        }
    done
    count=$(find "$directory/restart_data" -maxdepth 1 -type f \
        -name 'lustre_[0-9]*.dat' -size "${bytes}c" -printf '.' | wc -c)
    [[ "$count" -eq "$expected_count" ]] || {
        echo "ERROR: $directory has $count complete frames; expected $expected_count." >&2
        exit 3
    }
}

[[ -x "$PYTHON_BIN" ]] || { echo "ERROR: invalid PYTHON_BIN=$PYTHON_BIN" >&2; exit 2; }
for file in "$VIEW_BUILDER" "$RAW_RESTART_READER" "$CV_EXPORT_SCRIPT" \
            "$CV_LABEL_SCRIPT" "$CV_LOADER" "$CV_RUNNER"; do
    [[ -f "$file" ]] || { echo "ERROR: missing workflow file: $file" >&2; exit 2; }
done
[[ -f "$MFC_ROOT/toolchain/mfc/viz/reader.py" ]] || {
    echo "ERROR: pinned MFC reader is missing." >&2
    exit 2
}
if squeue -h -u "$SLURM_USER" -o '%j' 2>/dev/null | grep -Eq '^(mfc-cv-data|mfc-r31-ml)$'; then
    echo "ERROR: an MFC vision-dataset export is already active." >&2
    exit 4
fi

require_series "$RE1E4_ROOT/f180" 121 142560000 0 21600 180
require_series "$RE1E4_ROOT/f270" 121 320760000 0 32400 270
require_series "$LADDER_ROOT/re5e4" 61 142560000 0 21600 360
require_series "$LADDER_ROOT/re1e5" 61 142560000 0 21600 360

"$PYTHON_BIN" -m py_compile "$VIEW_BUILDER" "$RAW_RESTART_READER" \
    "$CV_EXPORT_SCRIPT" "$CV_LABEL_SCRIPT" "$CV_LOADER"
PYTHONPATH="$MFC_ROOT/toolchain${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" - <<'PY'
import numpy
import matplotlib
from PIL import Image
from mfc.viz.reader import assemble
if not callable(assemble):
    raise RuntimeError("MFC reader is not callable")
print("MFC_CV_PYTHON_PREFLIGHT=PASS")
PY

mkdir -p "$OUTPUT_PARENT"
available=$(df -PB1 "$OUTPUT_PARENT" | awk 'NR==2 {print $4}')
if [[ "$available" =~ ^[0-9]+$ ]] && ((available < 12000000000)); then
    echo "ERROR: less than 12 GB free for the vision dataset." >&2
    exit 5
fi

STAMP=$(date +%Y%m%d-%H%M%S)
ANALYSIS_ROOT=${ANALYSIS_ROOT:-$OUTPUT_PARENT/mfc_a40_cv_dataset_$STAMP}
[[ ! -e "$ANALYSIS_ROOT" ]] || {
    echo "ERROR: output already exists: $ANALYSIS_ROOT" >&2
    exit 5
}
mkdir -p "$ANALYSIS_ROOT/ml_dataset" "$ANALYSIS_ROOT/sources"
RE1E6_VIEW=$ANALYSIS_ROOT/sources/re1e6_retained
"$PYTHON_BIN" "$VIEW_BUILDER" --initial "$RE1E6_INITIAL" \
    --chain "$LONG_CHAIN" --output "$RE1E6_VIEW"

CASE_TABLE=$ANALYSIS_ROOT/case_table.tsv
{
    printf 'label\tdisplay\treynolds\tgrid\tdt\tcase_dir\tanalysis_start\trole\n'
    printf 're1e4_f180\tRe=1e4\t10000\tf180\t0.0002777777777777778\t%s\t0\tgrid_control\n' "$RE1E4_ROOT/f180"
    printf 're1e4_f270\tRe=1e4\t10000\tf270\t0.0001851851851851852\t%s\t0\tprimary\n' "$RE1E4_ROOT/f270"
    printf 're5e4_f180\tRe=5e4\t50000\tf180\t0.0002777777777777778\t%s\t0\tscreening\n' "$LADDER_ROOT/re5e4"
    printf 're1e5_f180\tRe=1e5\t100000\tf180\t0.0002777777777777778\t%s\t0\tscreening\n' "$LADDER_ROOT/re1e5"
    printf 're1e6_retained\tRe=1e6 retained\t1000000\tf270\t0.0001851851851851852\t%s\t0\tprimary_retained\n' "$RE1E6_VIEW"
} >"$CASE_TABLE"

"$PYTHON_BIN" "$CV_EXPORT_SCRIPT" --case-table "$CASE_TABLE" \
    --mfc-root "$MFC_ROOT" --output "$ANALYSIS_ROOT/ml_dataset" \
    --width 512 --height 512 --check-only | tee "$ANALYSIS_ROOT/inventory-preflight.json"

WORKFLOW_REV=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || printf UNKNOWN)
{
    printf 'status=PREFLIGHT_PASS\nworkflow_rev=%s\n' "$WORKFLOW_REV"
    printf 're1e4_root=%s\nladder_root=%s\n' "$RE1E4_ROOT" "$LADDER_ROOT"
    printf 're1e6_initial=%s\nlong_chain=%s\n' "$RE1E6_INITIAL" "$LONG_CHAIN"
    printf 're1e6_view=%s\n' "$RE1E6_VIEW"
} >"$ANALYSIS_ROOT/SOURCE_CASES.env"

common_export="ALL,ANALYSIS_ROOT=$ANALYSIS_ROOT,CASE_TABLE=$CASE_TABLE,PYTHON_BIN=$PYTHON_BIN,MFC_ROOT=$MFC_ROOT,CV_EXPORT_SCRIPT=$CV_EXPORT_SCRIPT"
CV_JOB=$(sbatch --parsable --nice=5000 --mem="$CV_MEMORY" --time="$CV_WALLTIME" \
    --constraint="$CONSTRAINT" --job-name=mfc-cv-data --chdir="$ANALYSIS_ROOT" \
    --mail-user="$MAIL_USER" --mail-type=END,FAIL \
    --output="$ANALYSIS_ROOT/slurm-cv-%j.out" --error="$ANALYSIS_ROOT/slurm-cv-%j.err" \
    --export="$common_export" "$CV_RUNNER")
CV_JOB=${CV_JOB%%;*}

{
    printf 'status=SUBMITTED\nworkflow_rev=%s\n' "$WORKFLOW_REV"
    printf 'analysis_root=%s\ncv_dataset_job=%s\n' "$ANALYSIS_ROOT" "$CV_JOB"
} >"$ANALYSIS_ROOT/SUBMISSION.env"

echo "MFC_CV_DATASET_PREFLIGHT=PASS"
echo "MFC_CV_DATASET_SUBMITTED=PASS"
echo "ANALYSIS_ROOT=$ANALYSIS_ROOT"
echo "CV_DATASET_JOB=$CV_JOB"
echo "WATCH=squeue -j $CV_JOB"
echo "TRAINING_DATASET=$ANALYSIS_ROOT/ml_dataset"
echo "FINAL=$ANALYSIS_ROOT/ml_dataset/DATASET_OK.txt"
