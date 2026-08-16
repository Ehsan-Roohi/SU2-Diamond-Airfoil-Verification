#!/usr/bin/env bash
set -Eeuo pipefail

# Submit a clean, inexpensive f180 rerun because the historical f180 output
# contains zero integrated loads in both IB-state and rank-0 Silo records.

DEFAULT_ROOT=/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification
ROOT="${ROOT:-$DEFAULT_ROOT}"
NTASKS="${NTASKS:-48}"
MEMORY="${MEMORY:-32G}"
WALLTIME="${WALLTIME:-12:00:00}"
QOS="${QOS:-default}"
CONSTRAINT="${CONSTRAINT:-intel&x86_64_v4}"
AFTER_JOB="${AFTER_JOB:-none}"
STEPS=48600
SAVE_EVERY=1944
EXPECTED_MFC_COMMIT=0c9a1d434410175ac483b8d71646455444e3b7eb

if [[ ! "$NTASKS" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: NTASKS must be a positive integer." >&2
    exit 2
fi
if [[ "$ROOT" != /* || "$ROOT" == / ]]; then
    echo "ERROR: ROOT must be a non-root absolute path." >&2
    exit 2
fi

if [[ -z "${MFC_ROOT:-}" && -x "$ROOT/third_party/MFC-0c9a1d43/mfc.sh" ]]; then
    MFC_ROOT="$ROOT/third_party/MFC-0c9a1d43"
fi
if [[ -z "${MFC_ROOT:-}" || ! -x "$MFC_ROOT/mfc.sh" ]]; then
    echo "ERROR: pinned MFC checkout was not found; set MFC_ROOT." >&2
    exit 2
fi
actual_mfc_commit=$(git -C "$MFC_ROOT" rev-parse HEAD 2>/dev/null || true)
if [[ -n "$actual_mfc_commit" && "$actual_mfc_commit" != "$EXPECTED_MFC_COMMIT" ]]; then
    echo "ERROR: MFC commit is $actual_mfc_commit; expected $EXPECTED_MFC_COMMIT." >&2
    exit 2
fi

STAMP=$(date +%Y%m%d-%H%M%S)
RUN_BASE="$ROOT/mfc_runs/fixed_ib_a40_f180_force_repair_jfm_$STAMP"
CASE_DIR="$RUN_BASE/f180_t13p5"
mkdir -p "$CASE_DIR"

RAW_BASE=https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-f405-grid-study/mfc_grid_convergence
curl -fL --retry 3 "$RAW_BASE/case_f180_force_repair.py" -o "$CASE_DIR/case.py"
curl -fL --retry 3 "$RAW_BASE/Diamond_Airfoil_2D_MFC.stl" \
    -o "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl"

python3 - "$CASE_DIR/case.py" <<'PY'
import json
import math
import subprocess
import sys

case = json.loads(subprocess.check_output([sys.executable, sys.argv[1]], text=True))
expected = {
    "m": 1979,
    "n": 1799,
    "t_step_start": 0,
    "t_step_stop": 48600,
    "t_step_save": 1944,
    "ib": "T",
    "ib_state_wrt": "T",
    "patch_ib(1)%slip": "T",
    "viscous": "F",
    "precision": "double",
}
for key, value in expected.items():
    if case.get(key) != value:
        raise SystemExit(f"ERROR: {key}={case.get(key)!r}; expected {value!r}")
if not math.isclose(case["dt"], 1.0 / 3600.0, rel_tol=0.0, abs_tol=1.0e-15):
    raise SystemExit(f"ERROR: unexpected dt={case['dt']!r}")
print("CASE_PREFLIGHT=PASS")
print("GRID=1980x1800")
print("PHYSICAL_TIME=13.5")
print("SAVE_DT=0.54")
PY

if [[ "$(grep -c 'facet normal' "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl")" -ne 2 ]]; then
    echo "ERROR: expected the validated two-triangle planar STL." >&2
    exit 2
fi

dependency_args=()
if [[ -n "$AFTER_JOB" && "$AFTER_JOB" != none ]]; then
    if [[ ! "$AFTER_JOB" =~ ^[0-9]+(:[0-9]+)*$ ]]; then
        echo "ERROR: AFTER_JOB must be none or colon-separated numeric job IDs." >&2
        exit 2
    fi
    dependency_args+=(--dependency="afterany:${AFTER_JOB}")
fi

SBATCH_FILE="$CASE_DIR/run_f180_force_repair.sbatch"
cat >"$SBATCH_FILE" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1

set -Eeuo pipefail
: "${CASE_DIR:?}"
: "${MFC_ROOT:?}"

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
MPI_LAUNCHER=$(command -v mpirun)
[[ -x "$MPI_LAUNCHER" ]] || { echo "ERROR: mpirun was not found." >&2; exit 5; }
echo "MPI_LAUNCHER=$MPI_LAUNCHER"
"$MPI_LAUNCHER" --version | head -2

cd "$MFC_ROOT"
mkdir -p build
LOCK_FILE="$MFC_ROOT/build/.mfc-a40-startup.lock"
if command -v flock >/dev/null 2>&1 && [[ "${MFC_LOCK_HELD:-0}" != 1 ]]; then
    export MFC_LOCK_HELD=1
    echo "Waiting for shared MFC runtime lock: $LOCK_FILE"
    exec flock -s -w 7200 "$LOCK_FILE" bash "$0"
fi

./mfc.sh validate "$CASE_DIR/case.py" 2>&1 | tee "$CASE_DIR/validate-f180-force-repair.log"

LOG="$CASE_DIR/mfc-f180-force-repair.log"
set +e
./mfc.sh run "$CASE_DIR/case.py" \
    -n "$SLURM_NTASKS" -j 1 --mpi --no-gpu --binary mpirun --no-build \
    -t pre_process simulation post_process 2>&1 | tee "$LOG"
mfc_status=${PIPESTATUS[0]}
set -e
if [[ "$mfc_status" -ne 0 ]]; then
    echo "ERROR: MFC f180 force-repair run failed with exit code $mfc_status." >&2
    exit "$mfc_status"
fi

grep -aq 'Number of 2D model boundary edges: *4' "$LOG" || {
    echo "ERROR: MFC did not detect four airfoil boundary edges." >&2
    exit 3
}
[[ -s "$CASE_DIR/restart_data/lustre_48600.dat" ]] || {
    echo "ERROR: final f180 restart was not written." >&2
    exit 3
}

python3 - "$CASE_DIR" <<'PY'
import math
import struct
import sys
from pathlib import Path

case = Path(sys.argv[1])
maximum = 0.0
for step in range(0, 48600 + 1, 1944):
    path = case / "restart_data" / f"ib_state_{step}.dat"
    payload = path.read_bytes()
    if len(payload) != 160:
        raise SystemExit(f"ERROR: invalid one-body IB-state file: {path}")
    time, force_x, force_y = struct.unpack("=20d", payload)[:3]
    if not math.isclose(time, step / 3600.0, abs_tol=5.0e-8):
        raise SystemExit(f"ERROR: invalid time in {path}: {time}")
    if time >= 8.64 - 1.0e-9:
        maximum = max(maximum, math.hypot(force_x, force_y))
if maximum <= 1.0e-10:
    raise SystemExit("ERROR: f180 completed but its late-time force history is zero")
print(f"F180_FORCE_GATE=PASS max_late_force={maximum:.16g}")
PY

touch "$CASE_DIR/RUN_OK_F180_FORCE_REPAIR.txt"
echo "RUN_OK_F180_FORCE_REPAIR=$CASE_DIR/RUN_OK_F180_FORCE_REPAIR.txt"
SBATCH

ENV_FILE="$RUN_BASE/submission.env"
: >"$ENV_FILE"
write_env() { printf '%s=%q\n' "$1" "$2" >>"$ENV_FILE"; }
write_env RUN_BASE "$RUN_BASE"
write_env CASE_DIR "$CASE_DIR"
write_env MFC_ROOT "$MFC_ROOT"
write_env MFC_COMMIT "${actual_mfc_commit:-directory-name-pinned-0c9a1d43}"
write_env NTASKS "$NTASKS"
write_env MEMORY "$MEMORY"
write_env WALLTIME "$WALLTIME"
write_env QOS "$QOS"
write_env CONSTRAINT "$CONSTRAINT"
write_env AFTER_JOB "$AFTER_JOB"

job_id=$(sbatch --parsable \
    --ntasks="$NTASKS" --mem="$MEMORY" --time="$WALLTIME" --qos="$QOS" \
    --constraint="$CONSTRAINT" "${dependency_args[@]}" \
    --job-name=mfc-a40-f180-force \
    --output="$CASE_DIR/slurm-%j.out" --error="$CASE_DIR/slurm-%j.err" \
    --export="ALL,CASE_DIR=$CASE_DIR,MFC_ROOT=$MFC_ROOT" "$SBATCH_FILE")
job_id="${job_id%%;*}"
if [[ ! "$job_id" =~ ^[0-9]+$ ]]; then
    echo "ERROR: unexpected sbatch response '$job_id'." >&2
    exit 4
fi
write_env JOB_ID "$job_id"

echo "RUN_BASE=$RUN_BASE"
echo "CASE_DIR=$CASE_DIR"
echo "ENV_FILE=$ENV_FILE"
echo "JOB_ID=$job_id"
echo "MONITOR: squeue -j $job_id; tail -f '$CASE_DIR/slurm-${job_id}.out'"
