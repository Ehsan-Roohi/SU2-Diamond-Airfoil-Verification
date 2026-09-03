#!/usr/bin/env bash
set -Eeuo pipefail
trap 'rc=$?; trap - ERR; echo "ERROR: control-volume-force submitter stopped at line $LINENO (exit $rc)." >&2; exit "$rc"' ERR

PROJECT_ROOT=${PROJECT_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}
ANALYSIS_PARENT=${ANALYSIS_PARENT:-/scratch4/workspace/roohie_umass_edu-mfc-a40-cv}
PYTHON_BIN=${PYTHON_BIN:-$(command -v python3)}
SLURM_USER=${SLURM_USER:-${USER:-$(id -un)}}
WORKFLOW_REV=${WORKFLOW_REV:-UNKNOWN}
MEMORY=${MEMORY:-12G}
WALLTIME=${WALLTIME:-04:00:00}
NICE=${NICE:-5000}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
analyzer="$script_dir/reconstruct_control_volume_forces.py"
runner="$script_dir/run_control_volume_forces.sbatch"

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
        t26_t31/RUN_OK_RESTART.txt) || {
            echo 'ERROR: completed Re=1e6 t26--31 source was not found.' >&2
            exit 2
        }
fi

require_raw() {
    local case_dir=$1
    local step=$2
    local expected_bytes=$3
    local marker=$4
    local field="$case_dir/restart_data/lustre_${step}.dat"
    [[ -s "$case_dir/$marker" ]] || { echo "ERROR: missing completion marker $case_dir/$marker" >&2; exit 3; }
    [[ -s "$field" ]] || { echo "ERROR: missing raw restart $field" >&2; exit 3; }
    [[ "$(stat -c %s "$field")" -eq "$expected_bytes" ]] || {
        echo "ERROR: wrong raw restart size: $field" >&2
        exit 3
    }
}

f180_bytes=$((5 * 1980 * 1800 * 8))
f270_bytes=$((5 * 2970 * 2700 * 8))
require_raw "$RE1E4_ROOT/f180" 10800 "$f180_bytes" RUN_OK_HIGH_VISCOSITY.txt
require_raw "$RE1E4_ROOT/f180" 21600 "$f180_bytes" RUN_OK_HIGH_VISCOSITY.txt
require_raw "$RE1E4_ROOT/f270" 16200 "$f270_bytes" RUN_OK_HIGH_VISCOSITY.txt
require_raw "$RE1E4_ROOT/f270" 32400 "$f270_bytes" RUN_OK_HIGH_VISCOSITY.txt
require_raw "$LADDER_ROOT/re5e4" 10800 "$f180_bytes" RUN_OK_VISCOSITY_LADDER.txt
require_raw "$LADDER_ROOT/re5e4" 21600 "$f180_bytes" RUN_OK_VISCOSITY_LADDER.txt
require_raw "$LADDER_ROOT/re1e5" 10800 "$f180_bytes" RUN_OK_VISCOSITY_LADDER.txt
require_raw "$LADDER_ROOT/re1e5" 21600 "$f180_bytes" RUN_OK_VISCOSITY_LADDER.txt
require_raw "$LONG_CHAIN/t26_t31" 140400 "$f270_bytes" RUN_OK_RESTART.txt
require_raw "$LONG_CHAIN/t26_t31" 167400 "$f270_bytes" RUN_OK_RESTART.txt

[[ -x "$PYTHON_BIN" ]] || { echo "ERROR: python3 is unavailable: $PYTHON_BIN" >&2; exit 2; }
[[ -f "$analyzer" && -f "$runner" ]] || { echo "ERROR: workflow files are incomplete: $script_dir" >&2; exit 2; }
[[ -d "$ANALYSIS_PARENT" && -w "$ANALYSIS_PARENT" ]] || { echo "ERROR: scratch output is unavailable: $ANALYSIS_PARENT" >&2; exit 4; }

if squeue -h -u "$SLURM_USER" -o '%j' 2>/dev/null | grep -Eq '^mfc-cv-force$'; then
    echo 'ERROR: an mfc-cv-force job is already active.' >&2
    exit 5
fi

"$PYTHON_BIN" -m py_compile "$analyzer"
"$PYTHON_BIN" - <<'PY'
import matplotlib
import numpy
print("MFC_CONTROL_VOLUME_FORCE_PYTHON_PREFLIGHT=PASS")
PY
"$PYTHON_BIN" "$analyzer" --self-test

stamp=$(date -u +%Y%m%d-%H%M%S)
output=${OUTPUT:-$ANALYSIS_PARENT/tim_colonius_control_volume_forces_$stamp}
[[ ! -e "$output" ]] || { echo "ERROR: output already exists: $output" >&2; exit 6; }
mkdir -p "$output"

{
    printf 'status=PREFLIGHT_PASS\n'
    printf 'workflow_rev=%s\n' "$WORKFLOW_REV"
    printf 'python=%s\n' "$PYTHON_BIN"
    printf 'method=CONTROL_VOLUME_MOMENTUM_BALANCE_FROM_RAW_FIELDS\n'
    printf 'native_force_status=UNAVAILABLE_NAN\n'
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
printf '%s\n' "$output" >"$ANALYSIS_PARENT/LAST_TIM_COLONIUS_CONTROL_VOLUME_FORCE_OUTPUT.txt"

printf 'MFC_CONTROL_VOLUME_FORCE_SUBMITTED=PASS\nJOB_ID=%s\nOUTPUT=%s\nWATCH=squeue -j %s\nCHECK=bash %s/unity_check_control_volume_forces.sh %s\nEXPECTED_ARCHIVE=%s/TIM_COLONIUS_CONTROL_VOLUME_FORCES_JOB%s.zip\n' \
    "$job_id" "$output" "$job_id" "$script_dir" "$output" "$ANALYSIS_PARENT" "$job_id"

