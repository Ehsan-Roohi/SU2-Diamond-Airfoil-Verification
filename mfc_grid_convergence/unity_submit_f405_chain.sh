#!/usr/bin/env bash
set -Eeuo pipefail

# Submit f405 as three restartable, afterok-chained Unity jobs.  The shorter
# 24-hour requests are substantially easier to backfill than one 72-hour job.

DEFAULT_ROOT=/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification
ROOT="${ROOT:-$DEFAULT_ROOT}"
NTASKS="${NTASKS:-48}"
MEMORY="${MEMORY:-120G}"
WALLTIME="${WALLTIME:-1-00:00:00}"
QOS="${QOS:-}"
CONSTRAINT="${CONSTRAINT:-intel&x86_64_v4}"
SAVE_EVERY=4374
SEGMENT_STARTS=(0 34992 69984)
SEGMENT_STOPS=(34992 69984 109350)
AFTER_JOB="${AFTER_JOB:-auto}"
EXPECTED_MFC_COMMIT=0c9a1d434410175ac483b8d71646455444e3b7eb

if [[ ! "$NTASKS" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: NTASKS must be a positive integer; received '$NTASKS'." >&2
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
RUN_BASE="$ROOT/mfc_runs/fixed_ib_a40_f405_chain_jfm_${STAMP}"
CASE_DIR="$RUN_BASE/f405_t13p5"
mkdir -p "$CASE_DIR"

# The case and exact planar STL are immutable inputs pinned to a reviewed
# GitHub commit.  The launcher itself may be improved without changing them.
RAW_BASE="https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/a10399ec9d9cf0b65bcb8eadd116054a15bbcc07/mfc_grid_convergence"
curl -fL --retry 3 "$RAW_BASE/case_f405_restartable.py" -o "$CASE_DIR/case.py"
curl -fL --retry 3 "$RAW_BASE/Diamond_Airfoil_2D_MFC.stl" \
    -o "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl"

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
segments = ((0, 34992), (34992, 69984), (69984, 109350))
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
            "4374",
        ],
        text=True,
    )
    case = json.loads(raw)
    expected = {
        "m": 4454,
        "n": 4049,
        "t_step_start": start,
        "t_step_stop": stop,
        "t_step_save": 4374,
        "ib": "T",
        "patch_ib(1)%slip": "T",
        "viscous": "F",
        "precision": "double",
    }
    for key, value in expected.items():
        if case.get(key) != value:
            raise SystemExit(
                f"ERROR: segment {index}: {key}={case.get(key)!r}; expected {value!r}"
            )
    if not math.isclose(case["dt"], 1.0 / 8100.0, abs_tol=1.0e-15):
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
print("GRID=4455x4050")
print("SEGMENTS=0:34992,34992:69984,69984:109350")
print("PHYSICAL_END_TIMES=4.32,8.64,13.5")
PY

echo "CASE_SHA256=$(sha256sum "$CASE_DIR/case.py" | awk '{print $1}')"
echo "STL_SHA256=$(sha256sum "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl" | awk '{print $1}')"
echo "MFC_COMMIT=$actual_mfc_commit"

if [[ "$AFTER_JOB" == auto ]]; then
    mapfile -t active_jobs < <(
        squeue -h -u "$USER" -o '%A|%j|%T' | \
            awk -F'|' '$2 ~ /^mfc-a40-/ && $3 ~ /^(RUNNING|PENDING|CONFIGURING)$/ {print $1}' | \
            sort -u
    )
    if (( ${#active_jobs[@]} )); then
        AFTER_JOB="$(IFS=:; echo "${active_jobs[*]}")"
    else
        AFTER_JOB=none
    fi
fi
if [[ -n "$AFTER_JOB" && "$AFTER_JOB" != none && \
      ! "$AFTER_JOB" =~ ^[0-9]+(:[0-9]+)*$ ]]; then
    echo "ERROR: AFTER_JOB must be auto, none, or colon-separated job IDs." >&2
    exit 2
fi

SBATCH_FILE="$CASE_DIR/run_f405_segment.sbatch"
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
exec 9>build/.mfc-a40-startup.lock
if command -v flock >/dev/null 2>&1; then
    flock -s -w 7200 9
fi

CASE_ARGS=(
    --start-step "$START_STEP"
    --stop-step "$STOP_STEP"
    --save-every "$SAVE_EVERY"
)
./mfc.sh validate "$CASE_DIR/case.py" -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/validate-f405-s${SEGMENT_INDEX}.log"

LOG="$CASE_DIR/mfc-f405-s${SEGMENT_INDEX}.log"
set +e
./mfc.sh run "$CASE_DIR/case.py" \
    -n "$SLURM_NTASKS" -j 1 --mpi --no-gpu --binary mpirun --no-build \
    -t pre_process simulation post_process -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$LOG"
mfc_status=${PIPESTATUS[0]}
set -e
if [[ "$mfc_status" -ne 0 ]]; then
    echo "MFC f405 segment $SEGMENT_INDEX failed with exit code $mfc_status" >&2
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
if (( STOP_STEP == 109350 )); then
    touch "$CASE_DIR/RUN_OK_F405.txt"
    echo "RUN_OK_F405=$CASE_DIR/RUN_OK_F405.txt"
fi
SBATCH

ENV_FILE="$RUN_BASE/submission.env"
cat >"$ENV_FILE" <<EOF
RUN_BASE=$RUN_BASE
CASE_DIR=$CASE_DIR
MFC_ROOT=$MFC_ROOT
MFC_COMMIT=$actual_mfc_commit
SAVE_EVERY=$SAVE_EVERY
NTASKS=$NTASKS
MEMORY=$MEMORY
WALLTIME=$WALLTIME
QOS=${QOS:-default}
CONSTRAINT=$CONSTRAINT
AFTER_JOB=$AFTER_JOB
EOF

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
for array_index in 0 1 2; do
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
    job_id="$({
        cd "$CASE_DIR"
        sbatch "${common_args[@]}" "${dependency_args[@]}" \
            --job-name="mfc-a40-f405-s${segment_index}" \
            --output="$CASE_DIR/slurm-s${segment_index}-%j.out" \
            --error="$CASE_DIR/slurm-s${segment_index}-%j.err" \
            --export="$export_values" "$SBATCH_FILE"
    })"
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
