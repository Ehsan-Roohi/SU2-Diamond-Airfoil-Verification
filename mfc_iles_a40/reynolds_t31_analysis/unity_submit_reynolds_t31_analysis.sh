#!/usr/bin/env bash
set -Eeuo pipefail
trap 'rc=$?; trap - ERR; echo "ERROR: Reynolds/t31 submitter stopped at line $LINENO (exit $rc)." >&2; exit "$rc"' ERR

PROJECT_ROOT=${PROJECT_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}
REPO_ROOT=${REPO_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification}
MFC_ROOT=${MFC_ROOT:-$REPO_ROOT/third_party/MFC-0c9a1d43-iles-portable-v3}
ANALYSIS_PARENT=${ANALYSIS_PARENT:-$PROJECT_ROOT/analysis}
MAIL_USER=${MAIL_USER:-roohie@umass.edu}
SLURM_USER=${SLURM_USER:-${USER:-$(id -un)}}
PYTHON_BIN=${PYTHON_BIN:-$(command -v python3)}
CONSTRAINT=${CONSTRAINT:-intel&x86_64_v4}
ARRAY_LIMIT=${ARRAY_LIMIT:-2}
PREP_MEMORY=${PREP_MEMORY:-8G}
PREP_WALLTIME=${PREP_WALLTIME:-02:00:00}
ANALYSIS_MEMORY=${ANALYSIS_MEMORY:-48G}
ANALYSIS_WALLTIME=${ANALYSIS_WALLTIME:-12:00:00}
VISUAL_MEMORY=${VISUAL_MEMORY:-48G}
VISUAL_WALLTIME=${VISUAL_WALLTIME:-12:00:00}
AGGREGATE_MEMORY=${AGGREGATE_MEMORY:-16G}
AGGREGATE_WALLTIME=${AGGREGATE_WALLTIME:-02:00:00}

BASE=$REPO_ROOT/mfc_iles_a40/reynolds_t31_analysis
ANALYZER=$REPO_ROOT/mfc_iles_a40/hll_production_analysis/analyze_mfc_hll_article.py
BUILD_SCRIPT=$BASE/build_long_view.py
RENDER_SCRIPT=$BASE/render_mfc_suite.py
AGGREGATE_SCRIPT=$BASE/aggregate_mfc_suite.py
PREP_RUNNER=$BASE/run_prepare_long_view.sbatch
ANALYSIS_RUNNER=$BASE/run_case_analysis.sbatch
VISUAL_RUNNER=$BASE/run_visuals.sbatch
AGGREGATE_RUNNER=$BASE/run_aggregate.sbatch

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
    if ! RE1E4_ROOT=$(latest_complete \
        "$PROJECT_ROOT/runs/mfc_iles_a40_tim_high_viscosity" \
        're1e4_t000_t060_*' \
        f180/RUN_OK_HIGH_VISCOSITY.txt \
        f270/RUN_OK_HIGH_VISCOSITY.txt); then
        echo "ERROR: no completed Re=1e4 f180/f270 pilot was found." >&2
        exit 2
    fi
fi

LADDER_ROOT=${LADDER_ROOT:-}
if [[ -z "$LADDER_ROOT" ]]; then
    if ! LADDER_ROOT=$(latest_complete \
        "$PROJECT_ROOT/runs/mfc_iles_a40_tim_viscosity_ladder" \
        'f180_re5e4_re1e5_t000_t060_*' \
        re5e4/RUN_OK_VISCOSITY_LADDER.txt \
        re1e5/RUN_OK_VISCOSITY_LADDER.txt); then
        echo "ERROR: no completed Re=5e4/Re=1e5 ladder was found." >&2
        exit 2
    fi
fi

RE1E6_INITIAL=${RE1E6_INITIAL:-}
if [[ -z "$RE1E6_INITIAL" ]]; then
    if ! RE1E6_INITIAL=$(latest_complete \
        "$PROJECT_ROOT/runs/mfc_iles_a40_fresh_hll_production" \
        'f270_t000_t0600_w5unmapped_hll_dt1_*' \
        RUN_OK_INITIAL.txt); then
        echo "ERROR: no completed Re=1e6 f270 initial HLL case was found." >&2
        exit 2
    fi
fi

LONG_CHAIN=${LONG_CHAIN:-}
if [[ -z "$LONG_CHAIN" ]]; then
    if ! LONG_CHAIN=$(latest_complete \
        "$PROJECT_ROOT/runs/mfc_iles_a40_hll_long_baseline" \
        'f270_t060_t360_hll_dt1_*' \
        t06_t11/RUN_OK_RESTART.txt \
        t11_t16/RUN_OK_RESTART.txt \
        t16_t21/RUN_OK_RESTART.txt \
        t21_t26/RUN_OK_RESTART.txt \
        t26_t31/RUN_OK_RESTART.txt); then
        echo "ERROR: no restart chain completed through t=31 was found." >&2
        exit 2
    fi
fi

require_checkpoint() {
    local directory=$1
    local step=$2
    local bytes=$3
    local marker=$4
    local field="$directory/restart_data/lustre_${step}.dat"
    local ib="$directory/restart_data/ib_state_${step}.dat"
    [[ -f "$directory/$marker" ]] || { echo "ERROR: missing $directory/$marker" >&2; exit 3; }
    [[ -f "$field" && "$(stat -c %s "$field")" -eq "$bytes" ]] || {
        echo "ERROR: missing or wrong-size checkpoint $field" >&2
        exit 3
    }
    [[ -f "$ib" && "$(stat -c %s "$ib")" -eq 160 ]] || {
        echo "ERROR: missing or wrong-size IB state $ib" >&2
        exit 3
    }
}

require_checkpoint "$RE1E4_ROOT/f180" 21600 142560000 RUN_OK_HIGH_VISCOSITY.txt
require_checkpoint "$RE1E4_ROOT/f270" 32400 320760000 RUN_OK_HIGH_VISCOSITY.txt
require_checkpoint "$LADDER_ROOT/re5e4" 21600 142560000 RUN_OK_VISCOSITY_LADDER.txt
require_checkpoint "$LADDER_ROOT/re1e5" 21600 142560000 RUN_OK_VISCOSITY_LADDER.txt
require_checkpoint "$RE1E6_INITIAL" 32400 320760000 RUN_OK_INITIAL.txt
require_checkpoint "$LONG_CHAIN/t26_t31" 167400 320760000 RUN_OK_RESTART.txt

[[ "$ARRAY_LIMIT" =~ ^[1-6]$ ]] || { echo "ERROR: ARRAY_LIMIT must be an integer from 1 to 6." >&2; exit 2; }
[[ -x "$PYTHON_BIN" ]] || { echo "ERROR: PYTHON_BIN is not executable: $PYTHON_BIN" >&2; exit 2; }
[[ -f "$MFC_ROOT/toolchain/mfc/viz/reader.py" ]] || { echo "ERROR: pinned MFC reader is missing." >&2; exit 2; }
for script in "$ANALYZER" "$BUILD_SCRIPT" "$RENDER_SCRIPT" "$AGGREGATE_SCRIPT" \
              "$PREP_RUNNER" "$ANALYSIS_RUNNER" "$VISUAL_RUNNER" "$AGGREGATE_RUNNER"; do
    [[ -f "$script" ]] || { echo "ERROR: workflow file is missing: $script" >&2; exit 2; }
done

if squeue -h -u "$SLURM_USER" -o '%j' 2>/dev/null | grep -Eq '^mfc-r31-'; then
    echo "ERROR: a Reynolds/t31 analysis workflow is already active." >&2
    exit 4
fi

"$PYTHON_BIN" -m py_compile "$ANALYZER" "$BUILD_SCRIPT" "$RENDER_SCRIPT" "$AGGREGATE_SCRIPT"
PYTHONPATH="$MFC_ROOT/toolchain${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" - <<'PY'
import os
import shutil
from pathlib import Path

import matplotlib
import numpy
from mfc.viz.reader import assemble

if not callable(assemble):
    raise RuntimeError("MFC reader import succeeded but assemble is not callable")

ffmpeg = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
if ffmpeg is None:
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise SystemExit(
            "ERROR: ffmpeg is unavailable. Load an ffmpeg module, install "
            "imageio-ffmpeg for PYTHON_BIN, or set FFMPEG_BIN=/absolute/path/to/ffmpeg."
        ) from exc
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

ffmpeg_path = Path(ffmpeg).expanduser()
if not ffmpeg_path.is_file() or not os.access(ffmpeg_path, os.X_OK):
    raise SystemExit(f"ERROR: ffmpeg executable is unavailable: {ffmpeg_path}")

print("MFC_REYNOLDS_T31_PYTHON_PREFLIGHT=PASS")
print(f"MFC_REYNOLDS_T31_FFMPEG={ffmpeg_path.resolve()}")
PY

mkdir -p "$ANALYSIS_PARENT"
available=$(df -PB1 "$ANALYSIS_PARENT" | awk 'NR==2 {print $4}')
if [[ "$available" =~ ^[0-9]+$ ]] && ((available < 5000000000)); then
    echo "ERROR: less than 5 GB free for analysis products." >&2
    exit 5
fi

STAMP=$(date +%Y%m%d-%H%M%S)
ANALYSIS_ROOT=${ANALYSIS_ROOT:-$ANALYSIS_PARENT/mfc_a40_reynolds_t31_$STAMP}
[[ ! -e "$ANALYSIS_ROOT" ]] || { echo "ERROR: ANALYSIS_ROOT already exists: $ANALYSIS_ROOT" >&2; exit 5; }
mkdir -p "$ANALYSIS_ROOT/cases" "$ANALYSIS_ROOT/visuals" "$ANALYSIS_ROOT/summary"
CASE_TABLE=$ANALYSIS_ROOT/case_table.tsv

{
    printf 'label\tdisplay\treynolds\tgrid\tdt\tcase_dir\tanalysis_start\trole\n'
    printf 're1e4_f180\tRe=1e4\t10000\tf180\t0.0002777777777777778\t%s\t3\tgrid_control\n' "$RE1E4_ROOT/f180"
    printf 're1e4_f270\tRe=1e4\t10000\tf270\t0.0001851851851851852\t%s\t3\tprimary\n' "$RE1E4_ROOT/f270"
    printf 're5e4_f180\tRe=5e4\t50000\tf180\t0.0002777777777777778\t%s\t3\tscreening\n' "$LADDER_ROOT/re5e4"
    printf 're1e5_f180\tRe=1e5\t100000\tf180\t0.0002777777777777778\t%s\t3\tscreening\n' "$LADDER_ROOT/re1e5"
    printf 're1e6_f270\tRe=1e6\t1000000\tf270\t0.0001851851851851852\t%s\t3\tprimary\n' "$RE1E6_INITIAL"
    printf 're1e6_long_t31\tRe=1e6-long\t1000000\tf270\t0.0001851851851851852\t%s\t6\tlong_baseline\n' "$ANALYSIS_ROOT/long_view"
} >"$CASE_TABLE"

WORKFLOW_REV=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || printf 'UNKNOWN')
{
    printf 'status=PREFLIGHT_PASS\n'
    printf 'workflow_rev=%s\n' "$WORKFLOW_REV"
    printf 'python=%s\n' "$PYTHON_BIN"
    printf 're1e4_root=%s\n' "$RE1E4_ROOT"
    printf 'ladder_root=%s\n' "$LADDER_ROOT"
    printf 're1e6_initial=%s\n' "$RE1E6_INITIAL"
    printf 'long_chain=%s\n' "$LONG_CHAIN"
} >"$ANALYSIS_ROOT/SOURCE_CASES.env"

"$PYTHON_BIN" "$ANALYZER" --self-test --output-dir "$ANALYSIS_ROOT/self_test" \
    >"$ANALYSIS_ROOT/self_test.log" 2>&1
test -s "$ANALYSIS_ROOT/self_test/self_test.json"

common_export="ALL,ANALYSIS_ROOT=$ANALYSIS_ROOT,CASE_TABLE=$CASE_TABLE,PYTHON_BIN=$PYTHON_BIN,MFC_ROOT=$MFC_ROOT"
PREP_JOB=$(sbatch --parsable --nice=5000 --mem="$PREP_MEMORY" --time="$PREP_WALLTIME" \
    --constraint="$CONSTRAINT" --job-name=mfc-r31-prep --chdir="$ANALYSIS_ROOT" \
    --output="$ANALYSIS_ROOT/slurm-prep-%j.out" --error="$ANALYSIS_ROOT/slurm-prep-%j.err" \
    --export="$common_export,BUILD_SCRIPT=$BUILD_SCRIPT,RE1E6_INITIAL=$RE1E6_INITIAL,LONG_CHAIN=$LONG_CHAIN" \
    "$PREP_RUNNER")
PREP_JOB=${PREP_JOB%%;*}

ANALYSIS_JOB=$(sbatch --parsable --nice=5000 --mem="$ANALYSIS_MEMORY" --time="$ANALYSIS_WALLTIME" \
    --constraint="$CONSTRAINT" --job-name=mfc-r31-case --chdir="$ANALYSIS_ROOT" \
    --array="0-5%$ARRAY_LIMIT" --dependency="afterok:$PREP_JOB" \
    --output="$ANALYSIS_ROOT/slurm-analysis-%A_%a.out" --error="$ANALYSIS_ROOT/slurm-analysis-%A_%a.err" \
    --export="$common_export,ANALYZER=$ANALYZER" "$ANALYSIS_RUNNER")
ANALYSIS_JOB=${ANALYSIS_JOB%%;*}

VISUAL_JOB=$(sbatch --parsable --nice=5000 --mem="$VISUAL_MEMORY" --time="$VISUAL_WALLTIME" \
    --constraint="$CONSTRAINT" --job-name=mfc-r31-visual --chdir="$ANALYSIS_ROOT" \
    --dependency="afterok:$ANALYSIS_JOB" \
    --output="$ANALYSIS_ROOT/slurm-visual-%j.out" --error="$ANALYSIS_ROOT/slurm-visual-%j.err" \
    --export="$common_export,RENDER_SCRIPT=$RENDER_SCRIPT" "$VISUAL_RUNNER")
VISUAL_JOB=${VISUAL_JOB%%;*}

AGGREGATE_JOB=$(sbatch --parsable --nice=5000 --mem="$AGGREGATE_MEMORY" --time="$AGGREGATE_WALLTIME" \
    --constraint="$CONSTRAINT" --job-name=mfc-r31-summary --chdir="$ANALYSIS_ROOT" \
    --dependency="afterok:$VISUAL_JOB" \
    --mail-user="$MAIL_USER" --mail-type=END,FAIL \
    --output="$ANALYSIS_ROOT/slurm-summary-%j.out" --error="$ANALYSIS_ROOT/slurm-summary-%j.err" \
    --export="$common_export,AGGREGATE_SCRIPT=$AGGREGATE_SCRIPT" "$AGGREGATE_RUNNER")
AGGREGATE_JOB=${AGGREGATE_JOB%%;*}

{
    printf 'status=SUBMITTED\n'
    printf 'workflow_rev=%s\n' "$WORKFLOW_REV"
    printf 'analysis_root=%s\n' "$ANALYSIS_ROOT"
    printf 'prepare_job=%s\n' "$PREP_JOB"
    printf 'analysis_array_job=%s\n' "$ANALYSIS_JOB"
    printf 'visual_job=%s\n' "$VISUAL_JOB"
    printf 'aggregate_job=%s\n' "$AGGREGATE_JOB"
} >"$ANALYSIS_ROOT/SUBMISSION.env"

echo "MFC_REYNOLDS_T31_PREFLIGHT=PASS"
echo "MFC_REYNOLDS_T31_SUBMITTED=PASS"
echo "ANALYSIS_ROOT=$ANALYSIS_ROOT"
echo "PREP_JOB=$PREP_JOB"
echo "ANALYSIS_ARRAY_JOB=$ANALYSIS_JOB"
echo "VISUAL_JOB=$VISUAL_JOB"
echo "AGGREGATE_JOB=$AGGREGATE_JOB"
echo "WATCH=squeue -j $PREP_JOB,$ANALYSIS_JOB,$VISUAL_JOB,$AGGREGATE_JOB"
echo "FINAL=$ANALYSIS_ROOT/ANALYSIS_COMPLETE.txt"
