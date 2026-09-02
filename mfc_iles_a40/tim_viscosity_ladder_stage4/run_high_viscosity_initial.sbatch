#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1

set -Eeuo pipefail

: "${CASE_DIR:?}"
: "${MFC_ILES_ROOT:?}"
: "${GRID:?}"
: "${RE_CHORD:?}"
: "${STOP_TIME:?}"
: "${SAVE_DT:?}"
: "${EXPECTED_STOP_STEP:?}"
: "${EXPECTED_SAVE_STEP:?}"
: "${EXPECTED_LUSTRE_BYTES:?}"

failure_marker="$CASE_DIR/RUN_FAILED_VISCOSITY_LADDER.txt"
on_error() {
    rc=$?
    trap - ERR
    printf 'status=FAILED\ngrid=%s\nRe_c=%s\nexit_code=%s\n' \
        "$GRID" "$RE_CHORD" "$rc" >"$failure_marker"
    exit "$rc"
}
trap on_error ERR

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1

MFC_PYTHON="$MFC_ILES_ROOT/build/venv/bin/python3"
[[ -x "$MFC_PYTHON" && -x "$MFC_ILES_ROOT/mfc.sh" ]] || {
    echo "ERROR: pinned MFC installation is incomplete" >&2
    exit 5
}
[[ "$(grep -c 'facet normal' "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl")" -eq 2 ]] || {
    echo "ERROR: invalid diamond STL" >&2
    exit 5
}

CASE_ARGS=(--mode initial --grid "$GRID" --start-time 0 --final-time "$STOP_TIME" \
    --save-dt "$SAVE_DT" --dt-factor 1 --format binary)

read -r start stop save solver order mapped viscous input_re < <(
    "$MFC_PYTHON" "$CASE_DIR/case.py" "${CASE_ARGS[@]}" |
        "$MFC_PYTHON" -c '
import json, sys
c=json.load(sys.stdin)
print(c["t_step_start"], c["t_step_stop"], c["t_step_save"],
      c["riemann_solver"], c["weno_order"], c["mapped_weno"],
      c["viscous"], 3.0*c["fluid_pp(1)%Re(1)"])
'
)
[[ "$start" -eq 0 && "$stop" -eq "$EXPECTED_STOP_STEP" && "$save" -eq "$EXPECTED_SAVE_STEP" ]] || {
    echo "ERROR: time-clock preflight mismatch" >&2
    exit 7
}
[[ "$solver" -eq 1 && "$order" -eq 5 && "$mapped" == F && "$viscous" == T ]] || {
    echo "ERROR: HLL/WENO/viscous preflight mismatch" >&2
    exit 7
}
"$MFC_PYTHON" - "$input_re" "$RE_CHORD" <<'PY'
import math, sys
assert math.isclose(float(sys.argv[1]), float(sys.argv[2]), rel_tol=1e-12)
PY

cd "$MFC_ILES_ROOT"
LOCK_FILE="$MFC_ILES_ROOT/build/.mfc-iles-a40.lock"
if command -v flock >/dev/null 2>&1 && [[ "${MFC_LOCK_HELD:-0}" != 1 ]]; then
    export MFC_LOCK_HELD=1
    exec flock -s -w 7200 "$LOCK_FILE" bash "$0"
fi

./mfc.sh validate "$CASE_DIR/case.py" -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/validate.log"
./mfc.sh run "$CASE_DIR/case.py" -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" \
    --mpi --no-gpu --binary mpirun --no-build -t pre_process -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/pre.log"

set +e
./mfc.sh run "$CASE_DIR/case.py" -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" \
    --mpi --no-gpu --binary mpirun --no-build -t simulation -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/simulation.log"
simulation_status=${PIPESTATUS[0]}
set -e

latest_step=0
while IFS= read -r file; do
    base=${file##*/}
    step=${base#lustre_}
    step=${step%.dat}
    [[ "$step" =~ ^[0-9]+$ ]] || continue
    if ((step <= EXPECTED_STOP_STEP && step % EXPECTED_SAVE_STEP == 0 && step > latest_step)); then
        latest_step=$step
    fi
done < <(find "$CASE_DIR/restart_data" -maxdepth 1 -type f -name 'lustre_[0-9]*.dat' -print)

if ((simulation_status != 0)); then
    printf 'status=SIMULATION_FAILED\ngrid=%s\nRe_c=%s\nlatest_step=%s\nexit_code=%s\n' \
        "$GRID" "$RE_CHORD" "$latest_step" "$simulation_status" >"$failure_marker"
    exit "$simulation_status"
fi
[[ "$latest_step" -eq "$EXPECTED_STOP_STEP" ]] || {
    echo "ERROR: run ended at step $latest_step" >&2
    exit 45
}
final_lustre="$CASE_DIR/restart_data/lustre_${EXPECTED_STOP_STEP}.dat"
final_ib="$CASE_DIR/restart_data/ib_state_${EXPECTED_STOP_STEP}.dat"
[[ "$(stat -c %s "$final_lustre")" -eq "$EXPECTED_LUSTRE_BYTES" ]] || {
    echo "ERROR: final field size mismatch" >&2
    exit 46
}
[[ "$(stat -c %s "$final_ib")" -eq 160 ]] || { echo "ERROR: final IB state size mismatch" >&2; exit 46; }
if grep -aEq 'ICFL is greater|MPI_ABORT|Errorcode:[[:space:]]*[1-9]|(^|[^[:alpha:]])[Nn][Aa][Nn]([^[:alpha:]]|$)' \
    "$CASE_DIR/simulation.log"; then
    echo "ERROR: numerical-failure signature found" >&2
    exit 47
fi
sha256sum "$final_lustre" "$final_ib" >"$CASE_DIR/final-checkpoint.sha256.txt"
printf 'status=PASS\ngrid=%s\nRe_c=%s\nstop_time=%s\nstop_step=%s\nmpi_ranks=%s\nsave_dt=%s\n' \
    "$GRID" "$RE_CHORD" "$STOP_TIME" "$EXPECTED_STOP_STEP" "$SLURM_NTASKS" "$SAVE_DT" |
    tee "$CASE_DIR/RUN_OK_VISCOSITY_LADDER.txt"
