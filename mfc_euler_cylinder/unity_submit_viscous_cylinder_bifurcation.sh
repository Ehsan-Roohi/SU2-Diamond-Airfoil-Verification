#!/usr/bin/env bash
set -Eeuo pipefail

# Diagnose the localized Re_D=10000 f180 viscous-cylinder failure with two
# matched short restarts.  Both branches start from the last conservative
# checkpoint before the density collapse, use half the previous time step,
# and differ only in the approximate Riemann solver (HLLC versus HLL).

REPOSITORY="Ehsan-Roohi/SU2-Diamond-Airfoil-Verification"
SOURCE_REF="agent/mfc-euler-cylinder-validation"
RAW_BASE="https://raw.githubusercontent.com/$REPOSITORY/$SOURCE_REF/mfc_euler_cylinder"
DEFAULT_SCRATCH=/scratch4/workspace/roohie_umass_edu-mfc-a40-cv

SOURCE_PROD_DIR="${SOURCE_PROD_DIR:-$DEFAULT_SCRATCH/mfc_viscous_cylinder_recovery/m2p7_re10000_f180_vcfl_dt4_20260903-235734/production}"
MFC_CYL_ROOT="${MFC_CYL_ROOT:-$DEFAULT_SCRATCH/MFC-0c9a1d43-viscous-cylinder-v1}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$DEFAULT_SCRATCH/mfc_viscous_cylinder_bifurcation}"
SOURCE_STEP="${SOURCE_STEP:-35964}"
SOURCE_DT="${SOURCE_DT:-0.00007507507507507508}"
START_TIME="${START_TIME:-2.7}"
FINAL_TIME="${FINAL_TIME:-3.1}"
SAVE_DT="${SAVE_DT:-0.025}"
CFL_COEFFICIENT="${CFL_COEFFICIENT:-0.025}"
MACH="${MACH:-2.7}"
REYNOLDS="${REYNOLDS:-10000}"
GRID="${GRID:-f180}"
RUN_SET_ID="${RUN_SET_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"

for path in "$SOURCE_PROD_DIR" "$MFC_CYL_ROOT" "$OUTPUT_ROOT"; do
    if [[ "$path" != /* || "$path" == / ]]; then
        echo "ERROR: configured paths must be non-root absolute paths: $path" >&2
        exit 2
    fi
done
[[ "$GRID" == f180 ]] || {
    echo "ERROR: this frozen diagnostic is defined only for GRID=f180." >&2
    exit 2
}
[[ -x "$MFC_CYL_ROOT/mfc.sh" ]] || {
    echo "ERROR: isolated MFC build tree is missing: $MFC_CYL_ROOT" >&2
    exit 2
}
SOURCE_RESTART="$SOURCE_PROD_DIR/restart_data"
for required in \
    "$SOURCE_RESTART/lustre_${SOURCE_STEP}.dat" \
    "$SOURCE_RESTART/ib_state_${SOURCE_STEP}.dat" \
    "$SOURCE_RESTART/lustre_x_cb.dat" \
    "$SOURCE_RESTART/lustre_y_cb.dat"; do
    [[ -s "$required" ]] || { echo "ERROR: missing source file: $required" >&2; exit 3; }
done

RUN_BASE="$OUTPUT_ROOT/m2p7_re10000_f180_t2p7_t3p1_cfl0p025_$RUN_SET_ID"
[[ ! -e "$RUN_BASE" ]] || { echo "ERROR: run directory exists: $RUN_BASE" >&2; exit 3; }
mkdir -p "$RUN_BASE"
CASE_SCRIPT="$RUN_BASE/case.py"
AUDIT_SCRIPT="$RUN_BASE/audit_viscous_checkpoint.py"
curl -fL --retry 3 "$RAW_BASE/case.py" -o "$CASE_SCRIPT"
curl -fL --retry 3 "$RAW_BASE/audit_viscous_checkpoint.py" -o "$AUDIT_SCRIPT"

read -r NEW_START_STEP FINAL_STEP SAVE_EVERY NEW_DT < <(
    python3 "$CASE_SCRIPT" \
        --mach "$MACH" --grid "$GRID" --reynolds "$REYNOLDS" \
        --restart --start-time "$START_TIME" --final-time "$FINAL_TIME" \
        --save-dt "$SAVE_DT" --cfl-coefficient "$CFL_COEFFICIENT" \
        --riemann-solver hllc --format silo | \
    python3 -c 'import json,sys; c=json.load(sys.stdin); print(c["t_step_start"],c["t_step_stop"],c["t_step_save"],repr(c["dt"]))'
)

python3 - "$SOURCE_STEP" "$SOURCE_DT" "$START_TIME" "$NEW_START_STEP" \
    "$NEW_DT" "$FINAL_STEP" "$FINAL_TIME" "$SAVE_EVERY" "$SAVE_DT" <<'PY'
import math
import sys

source_step, new_start, final_step, save_every = map(int, (sys.argv[1], sys.argv[4], sys.argv[6], sys.argv[8]))
source_dt, start_time, new_dt, final_time, save_dt = map(float, (sys.argv[2], sys.argv[3], sys.argv[5], sys.argv[7], sys.argv[9]))
checks = (
    (source_step * source_dt, start_time, "source checkpoint time"),
    (new_start * new_dt, start_time, "reindexed checkpoint time"),
    (final_step * new_dt, final_time, "final time"),
    (save_every * new_dt, save_dt, "save interval"),
    (source_dt / new_dt, 2.0, "time-step ratio"),
)
for actual, expected, label in checks:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=2.0e-12):
        raise SystemExit(f"ERROR: {label} mismatch: {actual} != {expected}")
if new_start != 2 * source_step:
    raise SystemExit("ERROR: the half-dt restart index must be twice SOURCE_STEP")
PY

prepare_branch() {
    local solver="$1"
    local case_dir="$RUN_BASE/$solver"
    mkdir -p "$case_dir/restart_data"
    cp "$CASE_SCRIPT" "$AUDIT_SCRIPT" "$case_dir/"
    cp --reflink=auto "$SOURCE_RESTART/lustre_${SOURCE_STEP}.dat" \
        "$case_dir/restart_data/lustre_${NEW_START_STEP}.dat"
    cp --reflink=auto "$SOURCE_RESTART/ib_state_${SOURCE_STEP}.dat" \
        "$case_dir/restart_data/ib_state_${NEW_START_STEP}.dat"
    for name in lustre_x_cb.dat lustre_y_cb.dat lustre_ib.dat; do
        [[ -s "$SOURCE_RESTART/$name" ]] || continue
        cp --reflink=auto "$SOURCE_RESTART/$name" "$case_dir/restart_data/$name"
    done
}
prepare_branch hllc
prepare_branch hll

JOB_SCRIPT="$RUN_BASE/run_bifurcation.sbatch"
cat >"$JOB_SCRIPT" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=24
#SBATCH --cpus-per-task=1
#SBATCH --mem=80G
#SBATCH --time=12:00:00
#SBATCH --constraint=intel&x86_64_v4
set -Eeuo pipefail

: "${RUN_BASE:?}"
: "${MFC_CYL_ROOT:?}"
: "${MACH:?}"
: "${REYNOLDS:?}"
: "${GRID:?}"
: "${START_TIME:?}"
: "${FINAL_TIME:?}"
: "${SAVE_DT:?}"
: "${CFL_COEFFICIENT:?}"
: "${NEW_DT:?}"
: "${NEW_START_STEP:?}"
: "${FINAL_STEP:?}"

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
mkdir -p "$MFC_CYL_ROOT/build"
LOCK_FILE="$MFC_CYL_ROOT/build/.mfc-euler-cylinder.lock"
if command -v flock >/dev/null 2>&1 && [[ "${MFC_LOCK_HELD:-0}" != 1 ]]; then
    export MFC_LOCK_HELD=1
    exec flock -x -w 7200 "$LOCK_FILE" bash "$0"
fi

run_branch() {
    local solver="$1"
    local case_dir="$RUN_BASE/$solver"
    local upper="${solver^^}"
    local case_args=(
        --mach "$MACH" --grid "$GRID" --reynolds "$REYNOLDS"
        --restart --start-time "$START_TIME" --final-time "$FINAL_TIME"
        --save-dt "$SAVE_DT" --cfl-coefficient "$CFL_COEFFICIENT"
        --riemann-solver "$solver" --format silo
    )
    local validate_status=0
    local simulation_status=0
    local post_status=not_attempted

    cd "$MFC_CYL_ROOT"
    set +e
    ./mfc.sh validate "$case_dir/case.py" -- "${case_args[@]}" \
        2>&1 | tee "$case_dir/validate.log"
    validate_status=${PIPESTATUS[0]}
    if (( validate_status == 0 )); then
        ./mfc.sh run "$case_dir/case.py" -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" \
            --mpi --no-gpu --binary mpirun --scratch \
            -t pre_process simulation -- "${case_args[@]}" \
            2>&1 | tee "$case_dir/simulation.log"
        simulation_status=${PIPESTATUS[0]}
    else
        simulation_status=97
    fi
    set -e

    "$MFC_CYL_ROOT/build/venv/bin/python3" "$case_dir/audit_viscous_checkpoint.py" \
        --restart-dir "$case_dir/restart_data" --grid "$GRID" --dt "$NEW_DT" \
        --reynolds "$REYNOLDS" --mach "$MACH" \
        --expected-final-step "$FINAL_STEP" --run-time-info "$case_dir/run_time.inf" \
        --output-tsv "$case_dir/CHECKPOINT_AUDIT.tsv" \
        --output-json "$case_dir/CHECKPOINT_AUDIT.json" \
        >"$case_dir/CHECKPOINT_AUDIT.stdout.json"

    local audit_status
    local latest_step
    read -r audit_status latest_step < <(
        "$MFC_CYL_ROOT/build/venv/bin/python3" - "$case_dir/CHECKPOINT_AUDIT.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data["diagnostic_status"], data["last_step"])
PY
    )

    if (( simulation_status == 0 )) && [[ "$audit_status" == COMPLETED_NUMERICAL_GATE ]]; then
        set +e
        ./mfc.sh run "$case_dir/case.py" -n 1 -j 1 --mpi --no-gpu \
            --binary mpirun -t post_process -- "${case_args[@]}" \
            2>&1 | tee "$case_dir/post-serial-silo.log"
        post_status=${PIPESTATUS[0]}
        set -e
        {
            printf 'status=COMPLETED_NUMERICAL_GATE\n'
            printf 'scope=numerical-stability diagnostic only; expert physical review required\n'
            printf 'riemann_solver=%s\n' "$solver"
            printf 'start_step=%s\nfinal_step=%s\nlatest_step=%s\n' \
                "$NEW_START_STEP" "$FINAL_STEP" "$latest_step"
            printf 'simulation_exit=%s\npostprocess_exit=%s\n' \
                "$simulation_status" "$post_status"
        } | tee "$case_dir/RUN_OK_${upper}_DIAGNOSTIC.txt"
    else
        {
            printf 'status=INCOMPLETE_OR_UNSTABLE\n'
            printf 'scope=numerical-stability diagnostic only; not physical validation\n'
            printf 'riemann_solver=%s\n' "$solver"
            printf 'validate_exit=%s\nsimulation_exit=%s\n' \
                "$validate_status" "$simulation_status"
            printf 'audit_status=%s\nlatest_step=%s\nexpected_final_step=%s\n' \
                "$audit_status" "$latest_step" "$FINAL_STEP"
        } | tee "$case_dir/RUN_FAILED_${upper}_DIAGNOSTIC.txt"
    fi

    {
        printf 'step\ttime\tbytes\n'
        find "$case_dir/restart_data" -maxdepth 1 -type f -name 'lustre_[0-9]*.dat' \
            -printf '%f\t%s\n' | sort -V | \
            awk -F'[_\t.]' -v dt="$NEW_DT" '{printf "%s\t%.16g\t%s\n",$2,$2*dt,$4}'
    } >"$case_dir/FIELD_INVENTORY.tsv"
    return 0
}

# Sequential execution prevents two case-specialized builds from mutating the
# same MFC build tree concurrently.
run_branch hllc
run_branch hll

for solver in hllc hll; do
    case_dir="$RUN_BASE/$solver"
    marker="$(find "$case_dir" -maxdepth 1 -type f \
        \( -name 'RUN_OK_*_DIAGNOSTIC.txt' -o -name 'RUN_FAILED_*_DIAGNOSTIC.txt' \) \
        -print -quit)"
    [[ -n "$marker" ]] || { echo "ERROR: no diagnostic marker for $solver" >&2; exit 98; }
    printf '%s\t%s\n' "$solver" "$(basename "$marker")"
done | tee "$RUN_BASE/BIFURCATION_SUMMARY.tsv"

cat >"$RUN_BASE/RUN_COMPLETE_BIFURCATION_DIAGNOSTIC.txt" <<'EOF'
status=DIAGNOSTIC_EXECUTED
scope=compare branch markers and CHECKPOINT_AUDIT.json; this is not physical validation
EOF
SBATCH

JOB_ID="$(sbatch --parsable \
    --job-name=mfc-cyl-hll-bifurcation \
    --output="$RUN_BASE/slurm-%j.out" --error="$RUN_BASE/slurm-%j.err" \
    --export="ALL,RUN_BASE=$RUN_BASE,MFC_CYL_ROOT=$MFC_CYL_ROOT,MACH=$MACH,REYNOLDS=$REYNOLDS,GRID=$GRID,START_TIME=$START_TIME,FINAL_TIME=$FINAL_TIME,SAVE_DT=$SAVE_DT,CFL_COEFFICIENT=$CFL_COEFFICIENT,NEW_DT=$NEW_DT,NEW_START_STEP=$NEW_START_STEP,FINAL_STEP=$FINAL_STEP" \
    "$JOB_SCRIPT")"
JOB_ID="${JOB_ID%%;*}"

ENV_FILE="$RUN_BASE/submission.env"
{
    printf 'STATUS=%q\n' SUBMITTED
    printf 'SOURCE_REF=%q\n' "$SOURCE_REF"
    printf 'SOURCE_PROD_DIR=%q\n' "$SOURCE_PROD_DIR"
    printf 'SOURCE_STEP=%q\n' "$SOURCE_STEP"
    printf 'RUN_BASE=%q\n' "$RUN_BASE"
    printf 'HLLC_DIR=%q\n' "$RUN_BASE/hllc"
    printf 'HLL_DIR=%q\n' "$RUN_BASE/hll"
    printf 'NEW_START_STEP=%q\n' "$NEW_START_STEP"
    printf 'FINAL_STEP=%q\n' "$FINAL_STEP"
    printf 'NEW_DT=%q\n' "$NEW_DT"
    printf 'BIFURCATION_JOB=%q\n' "$JOB_ID"
    printf 'JOBS=%q\n' "$JOB_ID"
} >"$ENV_FILE"

echo "RUN_BASE=$RUN_BASE"
echo "HLLC_DIR=$RUN_BASE/hllc"
echo "HLL_DIR=$RUN_BASE/hll"
echo "NEW_START_STEP=$NEW_START_STEP"
echo "FINAL_STEP=$FINAL_STEP"
echo "BIFURCATION_JOB=$JOB_ID"
echo "ENV_FILE=$ENV_FILE"
echo "NEXT: source '$ENV_FILE'; squeue -j \"\$BIFURCATION_JOB\""
