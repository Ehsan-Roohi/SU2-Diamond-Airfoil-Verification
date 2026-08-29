#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1

set -Eeuo pipefail

: "${STAGE_LABEL:?}"
: "${SOURCE_DIR:?}"
: "${CASE_DIR:?}"
: "${MFC_ILES_ROOT:?}"
: "${START_TIME:?}"
: "${STOP_TIME:?}"
: "${SAVE_DT:?}"
: "${EXPECTED_START_STEP:?}"
: "${EXPECTED_STOP_STEP:?}"
: "${EXPECTED_SAVE_STEP:?}"
: "${EXPECTED_LUSTRE_BYTES:?}"
: "${EXPECTED_IB_BYTES:?}"

failure_marker="$CASE_DIR/RUN_FAILED_${STAGE_LABEL}.txt"
on_error() {
    rc=$?
    trap - ERR
    printf 'status=FAILED\nstage=%s\nstart_time=%s\nstop_time=%s\nexit_code=%s\n' \
        "$STAGE_LABEL" "$START_TIME" "$STOP_TIME" "$rc" >"$failure_marker"
    exit "$rc"
}
trap on_error ERR

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1

MFC_PYTHON="$MFC_ILES_ROOT/build/venv/bin/python3"
[[ -x "$MFC_PYTHON" ]] || { echo "ERROR: missing $MFC_PYTHON" >&2; exit 5; }
[[ -x "$MFC_ILES_ROOT/mfc.sh" ]] || { echo "ERROR: missing MFC launcher" >&2; exit 5; }
[[ -f "$CASE_DIR/case.py" ]] || { echo "ERROR: missing case.py" >&2; exit 5; }
[[ "$(grep -c 'facet normal' "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl")" -eq 2 ]] || {
    echo "ERROR: the diamond STL must contain exactly two facets." >&2
    exit 5
}

if [[ "$EXPECTED_START_STEP" -eq 32400 ]]; then
    [[ -f "$SOURCE_DIR/RUN_OK_INITIAL.txt" ]] || {
        echo "ERROR: verified t=6 source marker is missing" >&2
        exit 6
    }
else
    [[ -f "$SOURCE_DIR/RUN_OK_RESTART.txt" ]] || {
        echo "ERROR: preceding continuation stage has no PASS marker" >&2
        exit 6
    }
fi

mkdir -p "$CASE_DIR/restart_data"
for stem in lustre ib_state; do
    source="$SOURCE_DIR/restart_data/${stem}_${EXPECTED_START_STEP}.dat"
    target="$CASE_DIR/restart_data/${stem}_${EXPECTED_START_STEP}.dat"
    [[ -s "$source" ]] || { echo "ERROR: missing source checkpoint $source" >&2; exit 6; }
    cp --reflink=auto "$source" "$target"
done
for name in lustre_x_cb.dat lustre_y_cb.dat; do
    source="$SOURCE_DIR/restart_data/$name"
    [[ -s "$source" ]] || { echo "ERROR: missing source grid $source" >&2; exit 6; }
    cp --reflink=auto "$source" "$CASE_DIR/restart_data/$name"
done

[[ "$(stat -c %s "$CASE_DIR/restart_data/lustre_${EXPECTED_START_STEP}.dat")" -eq "$EXPECTED_LUSTRE_BYTES" ]] || {
    echo "ERROR: unexpected source lustre size" >&2
    exit 6
}
[[ "$(stat -c %s "$CASE_DIR/restart_data/ib_state_${EXPECTED_START_STEP}.dat")" -eq "$EXPECTED_IB_BYTES" ]] || {
    echo "ERROR: unexpected source ib_state size" >&2
    exit 6
}

CASE_ARGS=(
    --mode restart
    --grid f270
    --start-time "$START_TIME"
    --final-time "$STOP_TIME"
    --save-dt "$SAVE_DT"
    --dt-factor 1
    --format binary
)

read -r actual_start actual_stop actual_save solver order mapped old_ic old_grid dt_value < <(
    "$MFC_PYTHON" "$CASE_DIR/case.py" "${CASE_ARGS[@]}" |
        "$MFC_PYTHON" -c '
import json, sys
c=json.load(sys.stdin)
print(c["t_step_start"], c["t_step_stop"], c["t_step_save"],
      c["riemann_solver"], c["weno_order"], c["mapped_weno"],
      c.get("old_ic"), c.get("old_grid"), repr(c["dt"]))
'
)
[[ "$actual_start" -eq "$EXPECTED_START_STEP" ]] || { echo "ERROR: start-step mismatch" >&2; exit 7; }
[[ "$actual_stop" -eq "$EXPECTED_STOP_STEP" ]] || { echo "ERROR: stop-step mismatch" >&2; exit 7; }
[[ "$actual_save" -eq "$EXPECTED_SAVE_STEP" ]] || { echo "ERROR: save-step mismatch" >&2; exit 7; }
[[ "$solver" -eq 1 && "$order" -eq 5 && "$mapped" == F ]] || { echo "ERROR: HLL/WENO configuration mismatch" >&2; exit 7; }
[[ "$old_ic" == T && "$old_grid" == T ]] || { echo "ERROR: restart flags are missing" >&2; exit 7; }

cd "$MFC_ILES_ROOT"
LOCK_FILE="$MFC_ILES_ROOT/build/.mfc-iles-a40.lock"
if command -v flock >/dev/null 2>&1 && [[ "${MFC_LOCK_HELD:-0}" != 1 ]]; then
    export MFC_LOCK_HELD=1
    exec flock -s -w 7200 "$LOCK_FILE" bash "$0"
fi

./mfc.sh validate "$CASE_DIR/case.py" -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/validate-${STAGE_LABEL}.log"

./mfc.sh run "$CASE_DIR/case.py" -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" \
    --mpi --no-gpu --binary mpirun --no-build -t pre_process -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/pre-${STAGE_LABEL}.log"

set +e
./mfc.sh run "$CASE_DIR/case.py" -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" \
    --mpi --no-gpu --binary mpirun --no-build -t simulation -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/simulation-${STAGE_LABEL}.log"
simulation_status=${PIPESTATUS[0]}
set -e

latest_step=$EXPECTED_START_STEP
while IFS= read -r file; do
    base=${file##*/}
    step=${base#lustre_}
    step=${step%.dat}
    [[ "$step" =~ ^[0-9]+$ ]] || continue
    if ((step >= EXPECTED_START_STEP && step <= EXPECTED_STOP_STEP && \
        (step - EXPECTED_START_STEP) % EXPECTED_SAVE_STEP == 0 && step > latest_step)); then
        latest_step=$step
    fi
done < <(find "$CASE_DIR/restart_data" -maxdepth 1 -type f -name 'lustre_[0-9]*.dat' -print)

if ((simulation_status != 0)); then
    printf 'status=SIMULATION_FAILED\nstage=%s\nlatest_step=%s\nlatest_time=%.12g\nexit_code=%s\n' \
        "$STAGE_LABEL" "$latest_step" "$($MFC_PYTHON -c 'import sys; print(int(sys.argv[1])*float(sys.argv[2]))' "$latest_step" "$dt_value")" \
        "$simulation_status" >"$failure_marker"
    exit "$simulation_status"
fi

[[ "$latest_step" -eq "$EXPECTED_STOP_STEP" ]] || {
    echo "ERROR: stage ended at $latest_step, expected $EXPECTED_STOP_STEP" >&2
    exit 45
}
final_lustre="$CASE_DIR/restart_data/lustre_${EXPECTED_STOP_STEP}.dat"
final_ib="$CASE_DIR/restart_data/ib_state_${EXPECTED_STOP_STEP}.dat"
[[ "$(stat -c %s "$final_lustre")" -eq "$EXPECTED_LUSTRE_BYTES" ]] || { echo "ERROR: final lustre size mismatch" >&2; exit 46; }
[[ "$(stat -c %s "$final_ib")" -eq "$EXPECTED_IB_BYTES" ]] || { echo "ERROR: final ib_state size mismatch" >&2; exit 46; }
if grep -aEq 'ICFL is greater|MPI_ABORT|Errorcode:[[:space:]]*[1-9]|(^|[^[:alpha:]])[Nn][Aa][Nn]([^[:alpha:]]|$)' \
    "$CASE_DIR/simulation-${STAGE_LABEL}.log"; then
    echo "ERROR: numerical-failure signature found in successful simulation log" >&2
    exit 47
fi

sha256sum "$final_lustre" "$final_ib" >"$CASE_DIR/final-checkpoint.sha256.txt"
printf 'status=PASS\nstage=%s\nstart_time=%s\nstop_time=%s\nstart_step=%s\nstop_step=%s\nmpi_ranks=%s\ndt=%s\nsave_dt=%s\n' \
    "$STAGE_LABEL" "$START_TIME" "$STOP_TIME" "$EXPECTED_START_STEP" \
    "$EXPECTED_STOP_STEP" "$SLURM_NTASKS" "$dt_value" "$SAVE_DT" |
    tee "$CASE_DIR/RUN_OK_RESTART.txt"
