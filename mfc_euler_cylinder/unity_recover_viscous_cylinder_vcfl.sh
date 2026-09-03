#!/usr/bin/env bash
set -Eeuo pipefail

# Recover the Mach-2.7/Re_D=10000 f180 cylinder after its VCFL stop.  The
# original run is read-only.  Its last complete checkpoint is re-indexed on
# the four-times-smaller viscous time step, crossed by a short stability gate,
# and then continued in two afterok-chained production segments.

REPOSITORY=Ehsan-Roohi/SU2-Diamond-Airfoil-Verification
SOURCE_REF=agent/mfc-euler-cylinder-validation
RAW_BASE="https://raw.githubusercontent.com/$REPOSITORY/$SOURCE_REF/mfc_euler_cylinder"

SOURCE_CASE_DIR="${SOURCE_CASE_DIR:-/scratch4/workspace/roohie_umass_edu-mfc-a40-cv/mfc_viscous_cylinder/runs/mfc_viscous_cylinder/m2p7_re10000_f180_20260903-222051/f180}"
MFC_CYL_ROOT="${MFC_CYL_ROOT:-/scratch4/workspace/roohie_umass_edu-mfc-a40-cv/MFC-0c9a1d43-viscous-cylinder-v1}"
RECOVERY_DATA_ROOT="${RECOVERY_DATA_ROOT:-/scratch4/workspace/roohie_umass_edu-mfc-a40-cv/mfc_viscous_cylinder_recovery}"
MACH="${MACH:-2.7}"
REYNOLDS="${REYNOLDS:-10000}"
GRID="${GRID:-f180}"
FINAL_TIME="${FINAL_TIME:-8.0}"
SOURCE_SAVE_DT="${SOURCE_SAVE_DT:-0.1}"
GATE_FINAL_TIME="${GATE_FINAL_TIME:-0.7}"
PROD_SPLIT_TIME="${PROD_SPLIT_TIME:-4.0}"
SAVE_DT="${SAVE_DT:-0.1}"
GATE_SAVE_DT="${GATE_SAVE_DT:-0.025}"
SOURCE_SAFE_STEP="${SOURCE_SAFE_STEP:-auto}"

for path in "$SOURCE_CASE_DIR" "$MFC_CYL_ROOT" "$RECOVERY_DATA_ROOT"; do
    [[ "$path" == /* && "$path" != / ]] || {
        echo "ERROR: unsafe or non-absolute path: $path" >&2
        exit 2
    }
done
[[ "$GRID" == f180 ]] || { echo "ERROR: this recovery is frozen for GRID=f180." >&2; exit 2; }
[[ -x "$MFC_CYL_ROOT/mfc.sh" ]] || {
    echo "ERROR: MFC runtime is missing: $MFC_CYL_ROOT" >&2
    exit 2
}
MFC_PYTHON="$MFC_CYL_ROOT/build/venv/bin/python3"
[[ -x "$MFC_PYTHON" ]] || { echo "ERROR: missing $MFC_PYTHON" >&2; exit 2; }
SOURCE_RESTART="$SOURCE_CASE_DIR/restart_data"
[[ -d "$SOURCE_RESTART" && -s "$SOURCE_CASE_DIR/case.py" ]] || {
    echo "ERROR: source case/restart directory is incomplete: $SOURCE_CASE_DIR" >&2
    exit 2
}

if [[ "$SOURCE_SAFE_STEP" == auto ]]; then
    SOURCE_SAFE_STEP="$($MFC_PYTHON - "$SOURCE_RESTART" <<'PY'
from collections import Counter
from pathlib import Path
import re, sys

root = Path(sys.argv[1])
states = []
for path in root.glob("lustre_[0-9]*.dat"):
    match = re.fullmatch(r"lustre_(\d+)\.dat", path.name)
    if match and path.stat().st_size > 0:
        step = int(match.group(1))
        ib = root / f"ib_state_{step}.dat"
        if ib.is_file() and ib.stat().st_size > 0:
            states.append((step, path.stat().st_size))
if not states:
    raise SystemExit("ERROR: no complete state/IB checkpoint pair found")
modal_size = Counter(size for _, size in states).most_common(1)[0][0]
complete = [step for step, size in states if size == modal_size]
print(max(complete))
PY
)"
fi
[[ "$SOURCE_SAFE_STEP" =~ ^[0-9]+$ ]] || {
    echo "ERROR: invalid SOURCE_SAFE_STEP=$SOURCE_SAFE_STEP" >&2
    exit 2
}

SOURCE_STATE="$SOURCE_RESTART/lustre_${SOURCE_SAFE_STEP}.dat"
SOURCE_IB_STATE="$SOURCE_RESTART/ib_state_${SOURCE_SAFE_STEP}.dat"
for file in "$SOURCE_STATE" "$SOURCE_IB_STATE" \
    "$SOURCE_RESTART/lustre_x_cb.dat" "$SOURCE_RESTART/lustre_y_cb.dat"; do
    [[ -s "$file" ]] || { echo "ERROR: required checkpoint input is missing: $file" >&2; exit 3; }
done

read -r SOURCE_DT SOURCE_SAFE_TIME < <(
    "$MFC_PYTHON" - "$SOURCE_CASE_DIR/case.py" "$MACH" "$GRID" \
        "$FINAL_TIME" "$SOURCE_SAVE_DT" "$REYNOLDS" "$SOURCE_SAFE_STEP" <<'PY'
import json, subprocess, sys
path, mach, grid, final_time, save_dt, reynolds, step = sys.argv[1:]
raw = subprocess.check_output(
    [sys.executable, path, "--mach", mach, "--grid", grid,
     "--final-time", final_time, "--save-dt", save_dt,
     "--reynolds", reynolds], text=True
)
case = json.loads(raw)
dt = float(case["dt"])
print(repr(dt), repr(int(step) * dt))
PY
)

STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_BASE="$RECOVERY_DATA_ROOT/m2p7_re10000_f180_vcfl_dt4_${STAMP}"
GATE_DIR="$RUN_BASE/gate"
PROD_DIR="$RUN_BASE/production"
mkdir -p "$GATE_DIR/restart_data" "$PROD_DIR/restart_data"
for destination in "$GATE_DIR" "$PROD_DIR"; do
    curl -fL --retry 3 "$RAW_BASE/case.py" -o "$destination/case.py"
done

read -r GATE_START_STEP GATE_STOP_STEP GATE_SAVE_EVERY NEW_DT < <(
    "$MFC_PYTHON" "$GATE_DIR/case.py" --mach "$MACH" --grid "$GRID" \
        --reynolds "$REYNOLDS" --restart --start-time "$SOURCE_SAFE_TIME" \
        --final-time "$GATE_FINAL_TIME" --save-dt "$GATE_SAVE_DT" --format silo | \
    "$MFC_PYTHON" -c 'import json,sys; c=json.load(sys.stdin); print(c["t_step_start"],c["t_step_stop"],c["t_step_save"],repr(c["dt"]))'
)
read -r PROD_START_STEP PROD_SPLIT_STEP PROD_SAVE_EVERY < <(
    "$MFC_PYTHON" "$PROD_DIR/case.py" --mach "$MACH" --grid "$GRID" \
        --reynolds "$REYNOLDS" --restart --start-time "$GATE_FINAL_TIME" \
        --final-time "$PROD_SPLIT_TIME" --save-dt "$SAVE_DT" --format silo | \
    "$MFC_PYTHON" -c 'import json,sys; c=json.load(sys.stdin); print(c["t_step_start"],c["t_step_stop"],c["t_step_save"])'
)
read -r PROD2_START_STEP FINAL_STEP < <(
    "$MFC_PYTHON" "$PROD_DIR/case.py" --mach "$MACH" --grid "$GRID" \
        --reynolds "$REYNOLDS" --restart --start-time "$PROD_SPLIT_TIME" \
        --final-time "$FINAL_TIME" --save-dt "$SAVE_DT" --format silo | \
    "$MFC_PYTHON" -c 'import json,sys; c=json.load(sys.stdin); print(c["t_step_start"],c["t_step_stop"])'
)

"$MFC_PYTHON" - "$SOURCE_DT" "$NEW_DT" "$SOURCE_SAFE_STEP" \
    "$GATE_START_STEP" "$GATE_STOP_STEP" "$PROD_START_STEP" \
    "$PROD_SPLIT_STEP" "$PROD2_START_STEP" <<'PY'
import math, sys
source_dt, new_dt = map(float, sys.argv[1:3])
source_step, gate_start, gate_stop, prod_start, split_stop, prod2_start = map(int, sys.argv[3:])
assert math.isclose(source_dt / new_dt, 4.0, rel_tol=0.0, abs_tol=1e-12)
assert gate_start == 4 * source_step
assert gate_stop == prod_start
assert split_stop == prod2_start
print("RECOVERY_PREFLIGHT=PASS")
PY

cp --reflink=auto "$SOURCE_STATE" "$GATE_DIR/restart_data/lustre_${GATE_START_STEP}.dat"
cp --reflink=auto "$SOURCE_IB_STATE" "$GATE_DIR/restart_data/ib_state_${GATE_START_STEP}.dat"
cp --reflink=auto "$SOURCE_RESTART/lustre_x_cb.dat" "$GATE_DIR/restart_data/lustre_x_cb.dat"
cp --reflink=auto "$SOURCE_RESTART/lustre_y_cb.dat" "$GATE_DIR/restart_data/lustre_y_cb.dat"
if [[ -s "$SOURCE_RESTART/lustre_ib.dat" ]]; then
    cp --reflink=auto "$SOURCE_RESTART/lustre_ib.dat" "$GATE_DIR/restart_data/lustre_ib.dat"
fi

STAGE_SBATCH="$RUN_BASE/run_viscous_recovery_stage.sbatch"
cat >"$STAGE_SBATCH" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1
set -Eeuo pipefail

: "${STAGE:?}"
: "${CASE_DIR:?}"
: "${MFC_CYL_ROOT:?}"
: "${MACH:?}"
: "${REYNOLDS:?}"
: "${START_TIME:?}"
: "${STOP_TIME:?}"
: "${SAVE_DT:?}"
: "${EXPECTED_START_STEP:?}"
: "${EXPECTED_STOP_STEP:?}"

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
MFC_PYTHON="$MFC_CYL_ROOT/build/venv/bin/python3"
CASE_ARGS=(--mach "$MACH" --grid f180 --reynolds "$REYNOLDS" --restart \
    --start-time "$START_TIME" --final-time "$STOP_TIME" --save-dt "$SAVE_DT" \
    --format silo)

if [[ "$STAGE" == production_1 ]]; then
    : "${PARENT_DIR:?}"
    : "${PARENT_START_STEP:?}"
    : "${PARENT_SAVE_STRIDE:?}"
    : "${PARENT_STOP_STEP:?}"
    mkdir -p "$CASE_DIR/restart_data"
    for source_step in "$PARENT_START_STEP" \
        "$((PARENT_START_STEP + PARENT_SAVE_STRIDE))" "$PARENT_STOP_STEP"; do
        for prefix in lustre ib_state; do
            source="$PARENT_DIR/restart_data/${prefix}_${source_step}.dat"
            target="$CASE_DIR/restart_data/${prefix}_${source_step}.dat"
            [[ -s "$source" ]] || { echo "ERROR: missing gate field $source" >&2; exit 6; }
            cp --reflink=auto "$source" "$target"
        done
    done
    for name in lustre_x_cb.dat lustre_y_cb.dat lustre_ib.dat; do
        [[ -s "$PARENT_DIR/restart_data/$name" ]] || continue
        cp --reflink=auto "$PARENT_DIR/restart_data/$name" "$CASE_DIR/restart_data/$name"
    done
fi

for required in \
    "$CASE_DIR/restart_data/lustre_${EXPECTED_START_STEP}.dat" \
    "$CASE_DIR/restart_data/ib_state_${EXPECTED_START_STEP}.dat"; do
    [[ -s "$required" ]] || { echo "ERROR: restart input is missing: $required" >&2; exit 6; }
done

cd "$MFC_CYL_ROOT"
LOCK_FILE="$MFC_CYL_ROOT/build/.mfc-euler-cylinder.lock"
if command -v flock >/dev/null 2>&1 && [[ "${MFC_LOCK_HELD:-0}" != 1 ]]; then
    export MFC_LOCK_HELD=1
    exec flock -s -w 7200 "$LOCK_FILE" bash "$0"
fi

./mfc.sh validate "$CASE_DIR/case.py" -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/validate-${STAGE}.log"
./mfc.sh run "$CASE_DIR/case.py" -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" \
    --mpi --no-gpu --binary mpirun --no-build -t pre_process -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/pre-${STAGE}.log"

set +e
./mfc.sh run "$CASE_DIR/case.py" -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" \
    --mpi --no-gpu --binary mpirun --no-build -t simulation -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/simulation-${STAGE}.log"
simulation_status=${PIPESTATUS[0]}
set -e

latest_step="$EXPECTED_START_STEP"
while IFS= read -r file; do
    step="$(basename "$file" .dat)"
    step="${step#lustre_}"
    [[ "$step" =~ ^[0-9]+$ ]] || continue
    (( step > latest_step && step <= EXPECTED_STOP_STEP )) && latest_step=$step
done < <(find "$CASE_DIR/restart_data" -maxdepth 1 -type f -name 'lustre_[0-9]*.dat' -print)

if (( simulation_status != 0 )); then
    printf 'status=SIMULATION_FAILED\nstage=%s\nlatest_step=%s\nexpected_stop=%s\n' \
        "$STAGE" "$latest_step" "$EXPECTED_STOP_STEP" | \
        tee "$CASE_DIR/RUN_FAILED_${STAGE^^}.txt"
    exit "$simulation_status"
fi
[[ -s "$CASE_DIR/restart_data/lustre_${EXPECTED_STOP_STEP}.dat" ]] || {
    echo "ERROR: final checkpoint $EXPECTED_STOP_STEP is missing." >&2
    exit 45
}
printf 'status=PASS\nstage=%s\nstart_step=%s\nstop_step=%s\n' \
    "$STAGE" "$EXPECTED_START_STEP" "$EXPECTED_STOP_STEP" | \
    tee "$CASE_DIR/RUN_OK_${STAGE^^}.txt"
SBATCH

POST_SBATCH="$RUN_BASE/run_viscous_recovery_post.sbatch"
cat >"$POST_SBATCH" <<'POST_SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=32G
#SBATCH --time=12:00:00
set -Eeuo pipefail

: "${PROD_DIR:?}"
: "${MFC_CYL_ROOT:?}"
: "${MACH:?}"
: "${REYNOLDS:?}"
: "${SOURCE_SAFE_TIME:?}"
: "${FINAL_TIME:?}"
: "${SAVE_DT:?}"
: "${EXPECTED_SNAPSHOTS:?}"
: "${FINAL_STEP:?}"

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
CASE_ARGS=(--mach "$MACH" --grid f180 --reynolds "$REYNOLDS" --restart \
    --start-time "$SOURCE_SAFE_TIME" --final-time "$FINAL_TIME" \
    --save-dt "$SAVE_DT" --format silo)
cd "$MFC_CYL_ROOT"
./mfc.sh run "$PROD_DIR/case.py" -n 1 -j 1 --mpi --no-gpu --binary mpirun \
    --no-build -t post_process -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$PROD_DIR/post-serial-silo.log"

[[ -s "$PROD_DIR/restart_data/lustre_${FINAL_STEP}.dat" ]] || {
    echo "ERROR: final restart is missing." >&2
    exit 45
}
root_count="$(find "$PROD_DIR/silo_hdf5/root" -maxdepth 1 -type f \
    -name 'collection_*.silo' | wc -l)"
(( root_count >= EXPECTED_SNAPSHOTS )) || {
    echo "ERROR: expected at least $EXPECTED_SNAPSHOTS root Silo collections; got $root_count." >&2
    exit 46
}
printf 'status=PASS\nsource_safe_time=%s\nfinal_time=%s\nsnapshots=%s\nfinal_restart=%s\nfinal_product=%s\n' \
    "$SOURCE_SAFE_TIME" "$FINAL_TIME" "$EXPECTED_SNAPSHOTS" \
    "$PROD_DIR/restart_data/lustre_${FINAL_STEP}.dat" \
    "$PROD_DIR/silo_hdf5/root/collection_${FINAL_STEP}.silo" | \
    tee "$PROD_DIR/RUN_OK_MFC_VISCOUS_CYLINDER_RECOVERED.txt"
POST_SBATCH

EXPECTED_SNAPSHOTS="$($MFC_PYTHON - "$SOURCE_SAFE_TIME" "$FINAL_TIME" "$SAVE_DT" <<'PY'
import sys
start, stop, save = map(float, sys.argv[1:])
print(round((stop - start) / save) + 1)
PY
)"

GATE_JOB="$(sbatch --parsable --ntasks=24 --mem=80G --time=02:00:00 \
    --constraint='intel&x86_64_v4' --job-name=mfc-cyl-vcfl-gate \
    --output="$GATE_DIR/slurm-%j.out" --error="$GATE_DIR/slurm-%j.err" \
    --export="ALL,STAGE=gate,CASE_DIR=$GATE_DIR,MFC_CYL_ROOT=$MFC_CYL_ROOT,MACH=$MACH,REYNOLDS=$REYNOLDS,START_TIME=$SOURCE_SAFE_TIME,STOP_TIME=$GATE_FINAL_TIME,SAVE_DT=$GATE_SAVE_DT,EXPECTED_START_STEP=$GATE_START_STEP,EXPECTED_STOP_STEP=$GATE_STOP_STEP" \
    "$STAGE_SBATCH")"
GATE_JOB="${GATE_JOB%%;*}"

PROD1_JOB="$(sbatch --parsable --ntasks=24 --mem=80G --time=18:00:00 \
    --constraint='intel&x86_64_v4' --dependency="afterok:$GATE_JOB" \
    --job-name=mfc-cyl-vcfl-p1 --output="$PROD_DIR/slurm-%j.out" \
    --error="$PROD_DIR/slurm-%j.err" \
    --export="ALL,STAGE=production_1,CASE_DIR=$PROD_DIR,PARENT_DIR=$GATE_DIR,PARENT_START_STEP=$GATE_START_STEP,PARENT_SAVE_STRIDE=$PROD_SAVE_EVERY,PARENT_STOP_STEP=$GATE_STOP_STEP,MFC_CYL_ROOT=$MFC_CYL_ROOT,MACH=$MACH,REYNOLDS=$REYNOLDS,START_TIME=$GATE_FINAL_TIME,STOP_TIME=$PROD_SPLIT_TIME,SAVE_DT=$SAVE_DT,EXPECTED_START_STEP=$PROD_START_STEP,EXPECTED_STOP_STEP=$PROD_SPLIT_STEP" \
    "$STAGE_SBATCH")"
PROD1_JOB="${PROD1_JOB%%;*}"

PROD2_JOB="$(sbatch --parsable --ntasks=24 --mem=80G --time=18:00:00 \
    --constraint='intel&x86_64_v4' --dependency="afterok:$PROD1_JOB" \
    --job-name=mfc-cyl-vcfl-p2 --output="$PROD_DIR/slurm-%j.out" \
    --error="$PROD_DIR/slurm-%j.err" \
    --export="ALL,STAGE=production_2,CASE_DIR=$PROD_DIR,MFC_CYL_ROOT=$MFC_CYL_ROOT,MACH=$MACH,REYNOLDS=$REYNOLDS,START_TIME=$PROD_SPLIT_TIME,STOP_TIME=$FINAL_TIME,SAVE_DT=$SAVE_DT,EXPECTED_START_STEP=$PROD2_START_STEP,EXPECTED_STOP_STEP=$FINAL_STEP" \
    "$STAGE_SBATCH")"
PROD2_JOB="${PROD2_JOB%%;*}"

POST_JOB="$(sbatch --parsable --dependency="afterok:$PROD2_JOB" \
    --job-name=mfc-cyl-vcfl-post --output="$PROD_DIR/slurm-post-%j.out" \
    --error="$PROD_DIR/slurm-post-%j.err" \
    --export="ALL,PROD_DIR=$PROD_DIR,MFC_CYL_ROOT=$MFC_CYL_ROOT,MACH=$MACH,REYNOLDS=$REYNOLDS,SOURCE_SAFE_TIME=$SOURCE_SAFE_TIME,FINAL_TIME=$FINAL_TIME,SAVE_DT=$SAVE_DT,EXPECTED_SNAPSHOTS=$EXPECTED_SNAPSHOTS,FINAL_STEP=$FINAL_STEP" \
    "$POST_SBATCH")"
POST_JOB="${POST_JOB%%;*}"

ENV_FILE="$RUN_BASE/submission.env"
{
    printf 'SOURCE_CASE_DIR=%q\n' "$SOURCE_CASE_DIR"
    printf 'RUN_BASE=%q\n' "$RUN_BASE"
    printf 'GATE_DIR=%q\n' "$GATE_DIR"
    printf 'PROD_DIR=%q\n' "$PROD_DIR"
    printf 'SOURCE_SAFE_STEP=%q\n' "$SOURCE_SAFE_STEP"
    printf 'SOURCE_SAFE_TIME=%q\n' "$SOURCE_SAFE_TIME"
    printf 'NEW_DT=%q\n' "$NEW_DT"
    printf 'GATE_JOB=%q\n' "$GATE_JOB"
    printf 'PROD1_JOB=%q\n' "$PROD1_JOB"
    printf 'PROD2_JOB=%q\n' "$PROD2_JOB"
    printf 'POST_JOB=%q\n' "$POST_JOB"
    printf 'JOBS=%q\n' "$GATE_JOB,$PROD1_JOB,$PROD2_JOB,$POST_JOB"
} >"$ENV_FILE"

echo "SOURCE_SAFE_STEP=$SOURCE_SAFE_STEP"
echo "SOURCE_SAFE_TIME=$SOURCE_SAFE_TIME"
echo "NEW_DT=$NEW_DT"
echo "RUN_BASE=$RUN_BASE"
echo "GATE_JOB=$GATE_JOB"
echo "PROD1_JOB=$PROD1_JOB"
echo "PROD2_JOB=$PROD2_JOB"
echo "POST_JOB=$POST_JOB"
echo "ENV_FILE=$ENV_FILE"
echo "NEXT: source '$ENV_FILE'; squeue -j \"\$JOBS\""
