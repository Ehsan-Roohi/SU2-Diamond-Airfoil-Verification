#!/usr/bin/env bash
set -Eeuo pipefail

# Submit the publication f608 case as five checkpoint-aligned Unity jobs.
# Each segment covers five physical save intervals (Delta t = 2.7), keeping
# individual requests below 48 hours while preserving the complete t=13.5 run.

DEFAULT_ROOT=/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification
ROOT="${ROOT:-$DEFAULT_ROOT}"
NTASKS="${NTASKS:-48}"
MEMORY="${MEMORY:-120G}"
WALLTIME="${WALLTIME:-2-00:00:00}"
QOS="${QOS:-}"
CONSTRAINT="${CONSTRAINT:-intel&x86_64_v4}"
AFTER_JOB="${AFTER_JOB:-none}"
SAVE_EVERY=6561
SEGMENT_STARTS=(0 32805 65610 98415 131220)
SEGMENT_STOPS=(32805 65610 98415 131220 164025)
EXPECTED_MFC_COMMIT=0c9a1d434410175ac483b8d71646455444e3b7eb
EXPECTED_CASE_SHA256=f524813a1c8a9b22757f15a5ca945e08fc4d72ae9d4f2b2a11d1d0de782f3ca4
EXPECTED_STL_SHA256=65ea8cb922a7c092df652f630cc16904fc4920c0559ad7eb8664918ea7d6f210

if [[ ! "$NTASKS" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: NTASKS must be a positive integer; received '$NTASKS'." >&2
    exit 2
fi
if [[ "$ROOT" != /* || "$ROOT" == / ]]; then
    echo "ERROR: ROOT must be a non-root absolute path." >&2
    exit 2
fi
if [[ -n "$AFTER_JOB" && "$AFTER_JOB" != none && \
      ! "$AFTER_JOB" =~ ^[0-9]+(:[0-9]+)*$ ]]; then
    echo "ERROR: AFTER_JOB must be none or colon-separated numeric job IDs." >&2
    exit 2
fi

if [[ -z "${MFC_ROOT:-}" && -x "$ROOT/third_party/MFC-0c9a1d43/mfc.sh" ]]; then
    MFC_ROOT="$ROOT/third_party/MFC-0c9a1d43"
fi
if [[ -z "${MFC_ROOT:-}" || ! -x "$MFC_ROOT/mfc.sh" ]]; then
    echo "ERROR: pinned MFC root $ROOT/third_party/MFC-0c9a1d43 was not found." >&2
    echo "Set MFC_ROOT to the checkout at commit $EXPECTED_MFC_COMMIT." >&2
    exit 2
fi
if git -C "$MFC_ROOT" rev-parse HEAD >/dev/null 2>&1; then
    actual_mfc_commit="$(git -C "$MFC_ROOT" rev-parse HEAD)"
    if [[ "$actual_mfc_commit" != "$EXPECTED_MFC_COMMIT" ]]; then
        echo "ERROR: MFC commit is $actual_mfc_commit; expected $EXPECTED_MFC_COMMIT." >&2
        exit 2
    fi
else
    actual_mfc_commit=directory-name-pinned-0c9a1d43
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_BASE="$ROOT/mfc_runs/fixed_ib_a40_f608_chain_jfm_${STAMP}"
CASE_DIR="$RUN_BASE/f608_t13p5"
mkdir -p "$CASE_DIR"

RAW_BASE=https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-f405-grid-study/mfc_grid_convergence
curl -fL --retry 3 "$RAW_BASE/case_f608_restartable.py" -o "$CASE_DIR/case.py"
curl -fL --retry 3 "$RAW_BASE/Diamond_Airfoil_2D_MFC.stl" \
    -o "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl"

printf '%s  %s\n' "$EXPECTED_CASE_SHA256" "$CASE_DIR/case.py" | sha256sum -c -
printf '%s  %s\n' "$EXPECTED_STL_SHA256" \
    "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl" | sha256sum -c -
if [[ "$(grep -c 'facet normal' "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl")" -ne 2 ]]; then
    echo "ERROR: expected the validated two-triangle planar STL." >&2
    exit 2
fi

python3 - "$CASE_DIR/case.py" <<'PY'
import json
import math
import subprocess
import sys

path = sys.argv[1]
segments = (
    (0, 32805),
    (32805, 65610),
    (65610, 98415),
    (98415, 131220),
    (131220, 164025),
)
for index, (start, stop) in enumerate(segments, 1):
    raw = subprocess.check_output(
        [
            sys.executable,
            path,
            "--start-step",
            str(start),
            "--stop-step",
            str(stop),
            "--save-every",
            "6561",
        ],
        text=True,
    )
    case = json.loads(raw)
    expected = {
        "m": 6681,
        "n": 6074,
        "t_step_start": start,
        "t_step_stop": stop,
        "t_step_save": 6561,
        "ib": "T",
        "ib_state_wrt": "T",
        "patch_ib(1)%slip": "T",
        "viscous": "F",
        "precision": "double",
    }
    for key, value in expected.items():
        if case.get(key) != value:
            raise SystemExit(
                f"ERROR: segment {index}: {key}={case.get(key)!r}; expected {value!r}"
            )
    if not math.isclose(case["dt"], 1.0 / 12150.0, abs_tol=1.0e-15):
        raise SystemExit(f"ERROR: segment {index}: unexpected dt={case['dt']!r}")
    if start == 0:
        if case.get("num_patches") != 1 or "old_ic" in case:
            raise SystemExit("ERROR: segment 1 must create the initial condition")
    else:
        restart = {"num_patches": 0, "old_ic": "T", "old_grid": "T", "t_step_old": 0}
        for key, value in restart.items():
            if case.get(key) != value:
                raise SystemExit(f"ERROR: segment {index}: invalid restart key {key}")
print("CHAIN_PREFLIGHT=PASS")
print("LEVEL=f608 EFFECTIVE_CELLS_PER_CHORD=607.5")
print("GRID=6682x6075")
print("DT=1/12150 SAVE_DT=0.54")
print("SEGMENTS=0:32805,32805:65610,65610:98415,98415:131220,131220:164025")
print("PHYSICAL_END_TIMES=2.7,5.4,8.1,10.8,13.5")
PY

echo "CASE_SHA256=$(sha256sum "$CASE_DIR/case.py" | awk '{print $1}')"
echo "STL_SHA256=$(sha256sum "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl" | awk '{print $1}')"
echo "MFC_COMMIT=$actual_mfc_commit"
echo "RESOURCE_ESTIMATE=48_MPI_ranks,120G_memory,about_33_hours_per_segment"

SBATCH_FILE="$CASE_DIR/run_f608_segment.sbatch"
cat >"$SBATCH_FILE" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1

set -Eeuo pipefail

: "${CASE_DIR:?}"
: "${MFC_ROOT:?}"
: "${SEGMENT_INDEX:?}"
: "${START_STEP:?}"
: "${STOP_STEP:?}"
: "${SAVE_EVERY:?}"

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1

MPI_LAUNCHER="$(command -v mpirun)"
if [[ -z "$MPI_LAUNCHER" || ! -x "$MPI_LAUNCHER" ]]; then
    echo "ERROR: mpirun was not found after loading openmpi/5.0.3." >&2
    exit 5
fi
echo "MPI_LAUNCHER=$MPI_LAUNCHER"
"$MPI_LAUNCHER" --version | head -2
echo "SEGMENT=$SEGMENT_INDEX START=$START_STEP STOP=$STOP_STEP"

if (( START_STEP > 0 )); then
    RESTART_FILE="$CASE_DIR/restart_data/lustre_${START_STEP}.dat"
    if [[ ! -s "$RESTART_FILE" ]]; then
        echo "ERROR: required restart $RESTART_FILE is missing." >&2
        exit 3
    fi
fi

cd "$MFC_ROOT"
mkdir -p build
LOCK_FILE="$MFC_ROOT/build/.mfc-a40-startup.lock"
if command -v flock >/dev/null 2>&1 && [[ "${MFC_LOCK_HELD:-0}" != 1 ]]; then
    export MFC_LOCK_HELD=1
    echo "Waiting for shared MFC runtime lock: $LOCK_FILE"
    exec flock -s -w 7200 "$LOCK_FILE" bash "$0"
fi

CASE_ARGS=(
    --start-step "$START_STEP"
    --stop-step "$STOP_STEP"
    --save-every "$SAVE_EVERY"
)
./mfc.sh validate "$CASE_DIR/case.py" -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/validate-f608-s${SEGMENT_INDEX}.log"

LOG="$CASE_DIR/mfc-f608-s${SEGMENT_INDEX}.log"
set +e
./mfc.sh run "$CASE_DIR/case.py" \
    -n "$SLURM_NTASKS" -j 1 --mpi --no-gpu --binary mpirun --no-build \
    -t pre_process simulation post_process -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$LOG"
mfc_status=${PIPESTATUS[0]}
set -e
if [[ "$mfc_status" -ne 0 ]]; then
    echo "ERROR: MFC f608 segment $SEGMENT_INDEX failed with exit code $mfc_status." >&2
    exit "$mfc_status"
fi

grep -am1 'Number of 2D model boundary edges' "$LOG"
grep -aq 'Number of 2D model boundary edges: *4' "$LOG" || {
    echo "ERROR: MFC did not detect four airfoil boundary edges." >&2
    exit 3
}
[[ -s "$CASE_DIR/restart_data/lustre_${STOP_STEP}.dat" ]] || {
    echo "ERROR: restart step $STOP_STEP was not written." >&2
    exit 3
}

touch "$CASE_DIR/SEGMENT_${SEGMENT_INDEX}_OK.txt"
if (( STOP_STEP == 164025 )); then
    python3 - "$CASE_DIR" <<'PY'
import math
import struct
import sys
from pathlib import Path

case = Path(sys.argv[1])
maximum = 0.0
for step in range(0, 164025 + 1, 6561):
    path = case / "restart_data" / f"ib_state_{step}.dat"
    payload = path.read_bytes()
    if len(payload) != 160:
        raise SystemExit(f"ERROR: invalid one-body IB-state file: {path}")
    time, force_x, force_y = struct.unpack("=20d", payload)[:3]
    if not all(math.isfinite(value) for value in (time, force_x, force_y)):
        raise SystemExit(f"ERROR: non-finite IB-state value in {path}")
    if not math.isclose(time, step / 12150.0, abs_tol=5.0e-8):
        raise SystemExit(f"ERROR: invalid time in {path}: {time}")
    if time >= 8.64 - 1.0e-9:
        maximum = max(maximum, math.hypot(force_x, force_y))
if maximum <= 1.0e-10:
    raise SystemExit("ERROR: f608 completed but its late-time force history is zero")
print(f"F608_FORCE_GATE=PASS max_late_force={maximum:.16g}")
PY
    touch "$CASE_DIR/RUN_OK_F608.txt"
    echo "RUN_OK_F608=$CASE_DIR/RUN_OK_F608.txt"
fi
SBATCH

ENV_FILE="$RUN_BASE/submission.env"
: >"$ENV_FILE"
write_env() {
    printf '%s=%q\n' "$1" "$2" >>"$ENV_FILE"
}
write_env RUN_BASE "$RUN_BASE"
write_env CASE_DIR "$CASE_DIR"
write_env MFC_ROOT "$MFC_ROOT"
write_env MFC_COMMIT "$actual_mfc_commit"
write_env SAVE_EVERY "$SAVE_EVERY"
write_env NTASKS "$NTASKS"
write_env MEMORY "$MEMORY"
write_env WALLTIME "$WALLTIME"
write_env QOS "$QOS"
write_env CONSTRAINT "$CONSTRAINT"
write_env AFTER_JOB "$AFTER_JOB"

common_args=(
    --parsable
    --ntasks="$NTASKS"
    --mem="$MEMORY"
    --time="$WALLTIME"
    --constraint="$CONSTRAINT"
)
if [[ -n "$QOS" ]]; then
    common_args+=(--qos="$QOS")
fi

previous_job=""
submitted_jobs=()

cancel_partial_submission() {
    if (( ${#submitted_jobs[@]} )); then
        echo "ERROR: cancelling partially submitted chain: ${submitted_jobs[*]}" >&2
        scancel "${submitted_jobs[@]}" || true
    fi
}

for array_index in "${!SEGMENT_STARTS[@]}"; do
    segment_index=$((array_index + 1))
    start_step="${SEGMENT_STARTS[$array_index]}"
    stop_step="${SEGMENT_STOPS[$array_index]}"
    dependency_args=()
    if (( segment_index == 1 )); then
        if [[ -n "$AFTER_JOB" && "$AFTER_JOB" != none ]]; then
            dependency_args+=(--dependency="afterany:${AFTER_JOB}")
        fi
    else
        dependency_args+=(--dependency="afterok:${previous_job}")
    fi

    export_values="ALL,CASE_DIR=$CASE_DIR,MFC_ROOT=$MFC_ROOT,SEGMENT_INDEX=$segment_index,START_STEP=$start_step,STOP_STEP=$stop_step,SAVE_EVERY=$SAVE_EVERY"
    if ! job_id="$({
        cd "$CASE_DIR"
        sbatch "${common_args[@]}" "${dependency_args[@]}" \
            --job-name="mfc-a40-f608-s${segment_index}" \
            --output="$CASE_DIR/slurm-s${segment_index}-%j.out" \
            --error="$CASE_DIR/slurm-s${segment_index}-%j.err" \
            --export="$export_values" "$SBATCH_FILE"
    })"; then
        cancel_partial_submission
        exit 4
    fi
    job_id="${job_id%%;*}"
    if [[ ! "$job_id" =~ ^[0-9]+$ ]]; then
        echo "ERROR: unexpected sbatch response '$job_id'." >&2
        cancel_partial_submission
        exit 4
    fi
    previous_job="$job_id"
    submitted_jobs+=("$job_id")
    echo "JOB_${segment_index}=$job_id" | tee -a "$ENV_FILE"
done

echo "JOBS=$(IFS=,; echo "${submitted_jobs[*]}")" >>"$ENV_FILE"
echo "RUN_BASE=$RUN_BASE"
echo "CASE_DIR=$CASE_DIR"
echo "ENV_FILE=$ENV_FILE"
echo "AFTER_JOB=$AFTER_JOB"
grep '^JOB_' "$ENV_FILE"
echo "STATUS: source '$ENV_FILE'; squeue -j \"\$JOBS\""
