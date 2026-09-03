#!/usr/bin/env bash
set -Eeuo pipefail
trap 'rc=$?; trap - ERR; echo "ERROR: native-force submitter stopped at line $LINENO (exit $rc)." >&2; exit "$rc"' ERR

PROJECT_ROOT=${PROJECT_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}
ANALYSIS_PARENT=${ANALYSIS_PARENT:-/scratch4/workspace/roohie_umass_edu-mfc-a40-cv}
PYTHON_BIN=${PYTHON_BIN:-$(command -v python3)}
SLURM_USER=${SLURM_USER:-${USER:-$(id -un)}}
WORKFLOW_REV=${WORKFLOW_REV:-UNKNOWN}
MEMORY=${MEMORY:-6G}
WALLTIME=${WALLTIME:-01:00:00}
NICE=${NICE:-5000}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
analyzer="$script_dir/extract_native_ib_forces.py"
runner="$script_dir/run_native_force_analysis.sbatch"

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
            echo 'ERROR: completed Re=1e4 f180/f270 source was not found.' >&2
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
            echo 'ERROR: completed Re=5e4/Re=1e5 source was not found.' >&2
            exit 2
        }
fi

RE1E6_INITIAL=${RE1E6_INITIAL:-}
if [[ -z "$RE1E6_INITIAL" ]]; then
    RE1E6_INITIAL=$(latest_complete \
        "$PROJECT_ROOT/runs/mfc_iles_a40_fresh_hll_production" \
        'f270_t000_t0600_w5unmapped_hll_dt1_*' \
        RUN_OK_INITIAL.txt) || {
            echo 'ERROR: completed Re=1e6 initial source was not found.' >&2
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
            echo 'ERROR: completed Re=1e6 chain through t=31 was not found.' >&2
            exit 2
        }
fi

require_native_endpoint() {
    local case_dir=$1
    local step=$2
    local marker=$3
    local record="$case_dir/restart_data/ib_state_${step}.dat"
    [[ -f "$case_dir/$marker" ]] || {
        echo "ERROR: missing completion marker $case_dir/$marker" >&2
        exit 3
    }
    [[ -f "$record" ]] || {
        echo "ERROR: missing native IB endpoint $record" >&2
        exit 3
    }
    [[ "$(stat -c %s "$record")" -eq 160 ]] || {
        echo "ERROR: native IB endpoint has wrong size: $record" >&2
        exit 3
    }
}

[[ -x "$PYTHON_BIN" ]] || { echo "ERROR: python3 is unavailable: $PYTHON_BIN" >&2; exit 2; }
[[ -f "$analyzer" && -f "$runner" ]] || { echo "ERROR: workflow files are incomplete in $script_dir" >&2; exit 2; }
[[ -d "$ANALYSIS_PARENT" && -w "$ANALYSIS_PARENT" ]] || {
    echo "ERROR: scratch output directory is unavailable: $ANALYSIS_PARENT" >&2
    exit 4
}

require_native_endpoint "$RE1E4_ROOT/f180" 21600 RUN_OK_HIGH_VISCOSITY.txt
require_native_endpoint "$RE1E4_ROOT/f270" 32400 RUN_OK_HIGH_VISCOSITY.txt
require_native_endpoint "$LADDER_ROOT/re5e4" 21600 RUN_OK_VISCOSITY_LADDER.txt
require_native_endpoint "$LADDER_ROOT/re1e5" 21600 RUN_OK_VISCOSITY_LADDER.txt
require_native_endpoint "$RE1E6_INITIAL" 32400 RUN_OK_INITIAL.txt
require_native_endpoint "$LONG_CHAIN/t26_t31" 167400 RUN_OK_RESTART.txt

if squeue -h -u "$SLURM_USER" -o '%j' 2>/dev/null | grep -Eq '^mfc-native-force$'; then
    echo 'ERROR: an mfc-native-force job is already active.' >&2
    exit 5
fi

"$PYTHON_BIN" -m py_compile "$analyzer"
"$PYTHON_BIN" - <<'PY'
import matplotlib
import numpy
print("MFC_NATIVE_FORCE_PYTHON_PREFLIGHT=PASS")
PY
"$PYTHON_BIN" "$analyzer" --self-test

stamp=$(date -u +%Y%m%d-%H%M%S)
output=${OUTPUT:-$ANALYSIS_PARENT/tim_colonius_native_forces_$stamp}
[[ ! -e "$output" ]] || { echo "ERROR: output already exists: $output" >&2; exit 6; }
mkdir -p "$output"

{
    printf 'status=PREFLIGHT_PASS\n'
    printf 'workflow_rev=%s\n' "$WORKFLOW_REV"
    printf 'python=%s\n' "$PYTHON_BIN"
    printf 're1e4_root=%s\n' "$RE1E4_ROOT"
    printf 'ladder_root=%s\n' "$LADDER_ROOT"
    printf 're1e6_initial=%s\n' "$RE1E6_INITIAL"
    printf 'long_chain=%s\n' "$LONG_CHAIN"
    printf 'submitted_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$output/SOURCE_CASES.env"

job_token=$(sbatch --parsable --nice="$NICE" --mem="$MEMORY" --time="$WALLTIME" \
    --chdir="$output" \
    --output="$output/slurm-%j.out" \
    --error="$output/slurm-%j.err" \
    --export="ALL,ANALYZER=$analyzer,PYTHON_BIN=$PYTHON_BIN,OUTPUT=$output,RE1E4_ROOT=$RE1E4_ROOT,LADDER_ROOT=$LADDER_ROOT,RE1E6_INITIAL=$RE1E6_INITIAL,LONG_CHAIN=$LONG_CHAIN" \
    "$runner")
job_id=${job_token%%;*}

printf '%s\n' "$job_id" >"$output/JOB_ID.txt"
printf '%s\n' "$output" >"$ANALYSIS_PARENT/LAST_TIM_COLONIUS_NATIVE_FORCE_OUTPUT.txt"

printf 'MFC_NATIVE_FORCE_SUBMITTED=PASS\nJOB_ID=%s\nOUTPUT=%s\nWATCH=squeue -j %s\nCHECK=bash %s/unity_check_native_force_analysis.sh %s\nEXPECTED_ARCHIVE=%s/TIM_COLONIUS_NATIVE_FORCES_JOB%s.zip\n' \
    "$job_id" "$output" "$job_id" "$script_dir" "$output" "$ANALYSIS_PARENT" "$job_id"
