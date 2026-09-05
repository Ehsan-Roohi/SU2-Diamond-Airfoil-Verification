#!/usr/bin/env bash
set -Eeuo pipefail

# Run one viscous-cylinder flux branch in its own Slurm allocation, then audit
# it in a separate afterany job.  The separate audit survives an MPI_ABORT in
# the simulation allocation and always leaves an explicit diagnostic marker.

REPOSITORY="Ehsan-Roohi/SU2-Diamond-Airfoil-Verification"
SOURCE_REF="agent/mfc-euler-cylinder-validation"
RAW_BASE="https://raw.githubusercontent.com/$REPOSITORY/$SOURCE_REF/mfc_euler_cylinder"
DEFAULT_SCRATCH=/scratch4/workspace/roohie_umass_edu-mfc-a40-cv
DEFAULT_PROJECT=/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data

SOURCE_PROD_DIR="${SOURCE_PROD_DIR:-$DEFAULT_SCRATCH/mfc_viscous_cylinder_recovery/m2p7_re10000_f180_vcfl_dt4_20260903-235734/production}"
MFC_CYL_ROOT="${MFC_CYL_ROOT:-$DEFAULT_SCRATCH/MFC-0c9a1d43-viscous-cylinder-v1}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$DEFAULT_PROJECT/runs/mfc_viscous_cylinder_flux_branch}"
SOURCE_STEP="${SOURCE_STEP:-35964}"
SOURCE_DT="${SOURCE_DT:-0.00007507507507507508}"
START_TIME="${START_TIME:-2.7}"
FINAL_TIME="${FINAL_TIME:-3.1}"
SAVE_DT="${SAVE_DT:-0.025}"
CFL_COEFFICIENT="${CFL_COEFFICIENT:-0.025}"
MACH="${MACH:-2.7}"
REYNOLDS="${REYNOLDS:-10000}"
GRID="${GRID:-f180}"
RIEMANN_SOLVER="${RIEMANN_SOLVER:-hll}"
RUN_SET_ID="${RUN_SET_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"

case "$RIEMANN_SOLVER" in
    hll|hllc) ;;
    *) echo "ERROR: RIEMANN_SOLVER must be hll or hllc." >&2; exit 2 ;;
esac
[[ "$GRID" == f180 ]] || {
    echo "ERROR: this frozen diagnostic is defined only for GRID=f180." >&2
    exit 2
}
for path in "$SOURCE_PROD_DIR" "$MFC_CYL_ROOT" "$OUTPUT_ROOT"; do
    if [[ "$path" != /* || "$path" == / ]]; then
        echo "ERROR: configured paths must be non-root absolute paths: $path" >&2
        exit 2
    fi
done
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
    [[ -s "$required" ]] || {
        echo "ERROR: missing source file: $required" >&2
        exit 3
    }
done

RUN_BASE="$OUTPUT_ROOT/m2p7_re10000_f180_t2p7_t3p1_cfl0p025_${RIEMANN_SOLVER}_$RUN_SET_ID"
[[ ! -e "$RUN_BASE" ]] || {
    echo "ERROR: run directory exists: $RUN_BASE" >&2
    exit 3
}
CASE_DIR="$RUN_BASE/$RIEMANN_SOLVER"
mkdir -p "$CASE_DIR/restart_data"
curl -fL --retry 3 "$RAW_BASE/case.py" -o "$CASE_DIR/case.py"
curl -fL --retry 3 "$RAW_BASE/audit_viscous_checkpoint.py" \
    -o "$CASE_DIR/audit_viscous_checkpoint.py"

read -r NEW_START_STEP FINAL_STEP SAVE_EVERY NEW_DT < <(
    python3 "$CASE_DIR/case.py" \
        --mach "$MACH" --grid "$GRID" --reynolds "$REYNOLDS" \
        --restart --start-time "$START_TIME" --final-time "$FINAL_TIME" \
        --save-dt "$SAVE_DT" --cfl-coefficient "$CFL_COEFFICIENT" \
        --riemann-solver "$RIEMANN_SOLVER" --format silo | \
    python3 -c 'import json,sys; c=json.load(sys.stdin); print(c["t_step_start"],c["t_step_stop"],c["t_step_save"],repr(c["dt"]))'
)

python3 - "$SOURCE_STEP" "$SOURCE_DT" "$START_TIME" "$NEW_START_STEP" \
    "$NEW_DT" "$FINAL_STEP" "$FINAL_TIME" "$SAVE_EVERY" "$SAVE_DT" <<'PY'
import math
import sys

source_step, new_start, final_step, save_every = map(
    int, (sys.argv[1], sys.argv[4], sys.argv[6], sys.argv[8])
)
source_dt, start_time, new_dt, final_time, save_dt = map(
    float, (sys.argv[2], sys.argv[3], sys.argv[5], sys.argv[7], sys.argv[9])
)
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
    raise SystemExit("ERROR: half-dt restart index must equal 2*SOURCE_STEP")
PY

cp --reflink=auto "$SOURCE_RESTART/lustre_${SOURCE_STEP}.dat" \
    "$CASE_DIR/restart_data/lustre_${NEW_START_STEP}.dat"
cp --reflink=auto "$SOURCE_RESTART/ib_state_${SOURCE_STEP}.dat" \
    "$CASE_DIR/restart_data/ib_state_${NEW_START_STEP}.dat"
for name in lustre_x_cb.dat lustre_y_cb.dat lustre_ib.dat; do
    [[ -s "$SOURCE_RESTART/$name" ]] || continue
    cp --reflink=auto "$SOURCE_RESTART/$name" "$CASE_DIR/restart_data/$name"
done

RUN_SCRIPT="$RUN_BASE/run_${RIEMANN_SOLVER}.sbatch"
cat >"$RUN_SCRIPT" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=24
#SBATCH --cpus-per-task=1
#SBATCH --mem=80G
#SBATCH --time=12:00:00
#SBATCH --constraint=intel&x86_64_v4
set -Eeuo pipefail

: "${CASE_DIR:?}" "${MFC_CYL_ROOT:?}" "${RIEMANN_SOLVER:?}"
: "${MACH:?}" "${REYNOLDS:?}" "${GRID:?}"
: "${START_TIME:?}" "${FINAL_TIME:?}" "${SAVE_DT:?}"
: "${CFL_COEFFICIENT:?}"

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
mkdir -p "$MFC_CYL_ROOT/build"
LOCK_FILE="$MFC_CYL_ROOT/build/.mfc-euler-cylinder.lock"
if command -v flock >/dev/null 2>&1 && [[ "${MFC_LOCK_HELD:-0}" != 1 ]]; then
    export MFC_LOCK_HELD=1
    exec flock -x -w 7200 "$LOCK_FILE" bash "$0"
fi

case_args=(
    --mach "$MACH" --grid "$GRID" --reynolds "$REYNOLDS"
    --restart --start-time "$START_TIME" --final-time "$FINAL_TIME"
    --save-dt "$SAVE_DT" --cfl-coefficient "$CFL_COEFFICIENT"
    --riemann-solver "$RIEMANN_SOLVER" --format silo
)

cd "$MFC_CYL_ROOT"
./mfc.sh validate "$CASE_DIR/case.py" -- "${case_args[@]}" \
    2>&1 | tee "$CASE_DIR/validate.log"
./mfc.sh run "$CASE_DIR/case.py" -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" \
    --mpi --no-gpu --binary mpirun --scratch \
    -t pre_process simulation -- "${case_args[@]}" \
    2>&1 | tee "$CASE_DIR/simulation.log"
SBATCH

RUN_JOB="$(sbatch --parsable \
    --job-name="mfc-cyl-${RIEMANN_SOLVER}-gate" \
    --output="$RUN_BASE/slurm-${RIEMANN_SOLVER}-%j.out" \
    --error="$RUN_BASE/slurm-${RIEMANN_SOLVER}-%j.err" \
    --export="ALL,CASE_DIR=$CASE_DIR,MFC_CYL_ROOT=$MFC_CYL_ROOT,RIEMANN_SOLVER=$RIEMANN_SOLVER,MACH=$MACH,REYNOLDS=$REYNOLDS,GRID=$GRID,START_TIME=$START_TIME,FINAL_TIME=$FINAL_TIME,SAVE_DT=$SAVE_DT,CFL_COEFFICIENT=$CFL_COEFFICIENT" \
    "$RUN_SCRIPT")"
RUN_JOB="${RUN_JOB%%;*}"

AUDIT_SCRIPT="$RUN_BASE/audit_${RIEMANN_SOLVER}.sbatch"
cat >"$AUDIT_SCRIPT" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --time=01:00:00
set -Eeuo pipefail

: "${CASE_DIR:?}" "${MFC_CYL_ROOT:?}" "${RIEMANN_SOLVER:?}"
: "${MACH:?}" "${REYNOLDS:?}" "${GRID:?}" "${NEW_DT:?}"
: "${FINAL_STEP:?}" "${START_TIME:?}" "${FINAL_TIME:?}"
: "${SAVE_DT:?}" "${CFL_COEFFICIENT:?}" "${RUN_JOB:?}"

PYTHON_BIN="$MFC_CYL_ROOT/build/venv/bin/python3"
[[ -x "$PYTHON_BIN" ]] || {
    echo "ERROR: MFC Python is missing: $PYTHON_BIN" >&2
    exit 2
}

set +e
"$PYTHON_BIN" "$CASE_DIR/audit_viscous_checkpoint.py" \
    --restart-dir "$CASE_DIR/restart_data" --grid "$GRID" --dt "$NEW_DT" \
    --reynolds "$REYNOLDS" --mach "$MACH" \
    --expected-final-step "$FINAL_STEP" --run-time-info "$CASE_DIR/run_time.inf" \
    --output-tsv "$CASE_DIR/CHECKPOINT_AUDIT.tsv" \
    --output-json "$CASE_DIR/CHECKPOINT_AUDIT.json" \
    >"$CASE_DIR/CHECKPOINT_AUDIT.stdout.json" \
    2>"$CASE_DIR/CHECKPOINT_AUDIT.stderr.txt"
AUDIT_EXIT=$?
set -e

if (( AUDIT_EXIT == 0 )) && [[ -s "$CASE_DIR/CHECKPOINT_AUDIT.json" ]]; then
    read -r AUDIT_STATUS LATEST_STEP < <(
        "$PYTHON_BIN" - "$CASE_DIR/CHECKPOINT_AUDIT.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data["diagnostic_status"], data["last_step"])
PY
    )
else
    AUDIT_STATUS=AUDIT_FAILED
    LATEST_STEP=unknown
fi

RUN_STATE="$(sacct -j "$RUN_JOB" -X -n -P --format=State | head -n 1 | cut -d'|' -f1)"
RUN_EXIT="$(sacct -j "$RUN_JOB" -X -n -P --format=ExitCode | head -n 1 | cut -d'|' -f1)"
UPPER="${RIEMANN_SOLVER^^}"

if [[ "$AUDIT_STATUS" == COMPLETED_NUMERICAL_GATE ]]; then
    POST_EXIT=not_attempted
    module purge
    module load openmpi/5.0.3
    export OMP_NUM_THREADS=1
    case_args=(
        --mach "$MACH" --grid "$GRID" --reynolds "$REYNOLDS"
        --restart --start-time "$START_TIME" --final-time "$FINAL_TIME"
        --save-dt "$SAVE_DT" --cfl-coefficient "$CFL_COEFFICIENT"
        --riemann-solver "$RIEMANN_SOLVER" --format silo
    )
    cd "$MFC_CYL_ROOT"
    set +e
    ./mfc.sh run "$CASE_DIR/case.py" -n 1 -j 1 --mpi --no-gpu \
        --binary mpirun -t post_process -- "${case_args[@]}" \
        2>&1 | tee "$CASE_DIR/post-serial-silo.log"
    POST_EXIT=${PIPESTATUS[0]}
    set -e
    {
        printf 'status=COMPLETED_NUMERICAL_GATE\n'
        printf 'scope=numerical-stability diagnostic only; physical review required\n'
        printf 'riemann_solver=%s\n' "$RIEMANN_SOLVER"
        printf 'run_job=%s\nrun_state=%s\nrun_exit=%s\n' \
            "$RUN_JOB" "$RUN_STATE" "$RUN_EXIT"
        printf 'latest_step=%s\nexpected_final_step=%s\n' \
            "$LATEST_STEP" "$FINAL_STEP"
        printf 'postprocess_exit=%s\n' "$POST_EXIT"
    } | tee "$CASE_DIR/RUN_OK_${UPPER}_DIAGNOSTIC.txt"
else
    {
        printf 'status=INCOMPLETE_OR_UNSTABLE\n'
        printf 'scope=numerical-stability diagnostic only; not physical validation\n'
        printf 'riemann_solver=%s\n' "$RIEMANN_SOLVER"
        printf 'run_job=%s\nrun_state=%s\nrun_exit=%s\n' \
            "$RUN_JOB" "$RUN_STATE" "$RUN_EXIT"
        printf 'audit_exit=%s\naudit_status=%s\n' "$AUDIT_EXIT" "$AUDIT_STATUS"
        printf 'latest_step=%s\nexpected_final_step=%s\n' \
            "$LATEST_STEP" "$FINAL_STEP"
    } | tee "$CASE_DIR/RUN_FAILED_${UPPER}_DIAGNOSTIC.txt"
fi

{
    printf 'step\ttime\tbytes\n'
    find "$CASE_DIR/restart_data" -maxdepth 1 -type f \
        -name 'lustre_[0-9]*.dat' -printf '%f\t%s\n' | sort -V | \
        awk -F'[_\t.]' -v dt="$NEW_DT" \
        '{printf "%s\t%.16g\t%s\n",$2,$2*dt,$4}'
} >"$CASE_DIR/FIELD_INVENTORY.tsv"

printf 'status=AUDIT_COMPLETE\nriemann_solver=%s\n' "$RIEMANN_SOLVER" \
    >"$CASE_DIR/AUDIT_COMPLETE.txt"
SBATCH

AUDIT_JOB="$(sbatch --parsable \
    --dependency="afterany:$RUN_JOB" \
    --job-name="mfc-cyl-${RIEMANN_SOLVER}-audit" \
    --output="$RUN_BASE/slurm-audit-%j.out" \
    --error="$RUN_BASE/slurm-audit-%j.err" \
    --export="ALL,CASE_DIR=$CASE_DIR,MFC_CYL_ROOT=$MFC_CYL_ROOT,RIEMANN_SOLVER=$RIEMANN_SOLVER,MACH=$MACH,REYNOLDS=$REYNOLDS,GRID=$GRID,NEW_DT=$NEW_DT,FINAL_STEP=$FINAL_STEP,START_TIME=$START_TIME,FINAL_TIME=$FINAL_TIME,SAVE_DT=$SAVE_DT,CFL_COEFFICIENT=$CFL_COEFFICIENT,RUN_JOB=$RUN_JOB" \
    "$AUDIT_SCRIPT")"
AUDIT_JOB="${AUDIT_JOB%%;*}"

ENV_FILE="$RUN_BASE/submission.env"
{
    printf 'STATUS=%q\n' SUBMITTED
    printf 'SOURCE_REF=%q\n' "$SOURCE_REF"
    printf 'SOURCE_PROD_DIR=%q\n' "$SOURCE_PROD_DIR"
    printf 'SOURCE_STEP=%q\n' "$SOURCE_STEP"
    printf 'RUN_BASE=%q\n' "$RUN_BASE"
    printf 'CASE_DIR=%q\n' "$CASE_DIR"
    printf 'RIEMANN_SOLVER=%q\n' "$RIEMANN_SOLVER"
    printf 'NEW_START_STEP=%q\n' "$NEW_START_STEP"
    printf 'FINAL_STEP=%q\n' "$FINAL_STEP"
    printf 'NEW_DT=%q\n' "$NEW_DT"
    printf 'RUN_JOB=%q\n' "$RUN_JOB"
    printf 'AUDIT_JOB=%q\n' "$AUDIT_JOB"
    printf 'JOBS=%q\n' "$RUN_JOB,$AUDIT_JOB"
} >"$ENV_FILE"

echo "RUN_BASE=$RUN_BASE"
echo "CASE_DIR=$CASE_DIR"
echo "RIEMANN_SOLVER=$RIEMANN_SOLVER"
echo "RUN_JOB=$RUN_JOB"
echo "AUDIT_JOB=$AUDIT_JOB"
echo "ENV_FILE=$ENV_FILE"
echo "WATCH=squeue -j $RUN_JOB,$AUDIT_JOB"
