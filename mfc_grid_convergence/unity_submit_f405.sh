#!/usr/bin/env bash
set -Eeuo pipefail

# Submit the third, geometrically refined MFC grid for the JFM study.
#
# Defaults:
#   f405 = 4455 x 4050 cells
#   dt = 1/8100, t_stop = 13.5
#   save interval = 0.54
#   48 MPI ranks, 120 GiB, 72 hours on an Intel AVX-512 CPU node
#
# The run is created in a new timestamped directory, uses --no-build, and by
# default receives an afterany dependency on active mfc-a40-* jobs.  These
# safeguards prevent it from touching or competing with an existing f270
# continuation.

DEFAULT_ROOT=/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification
ROOT="${ROOT:-$DEFAULT_ROOT}"
NTASKS="${NTASKS:-48}"
MEMORY="${MEMORY:-120G}"
WALLTIME="${WALLTIME:-3-00:00:00}"
QOS="${QOS:-long}"
CONSTRAINT="${CONSTRAINT:-intel&x86_64_v4}"
STEPS="${STEPS:-109350}"
SAVE_EVERY="${SAVE_EVERY:-4374}"
AFTER_JOB="${AFTER_JOB:-auto}"
EXPECTED_MFC_COMMIT=0c9a1d434410175ac483b8d71646455444e3b7eb

for value_name in NTASKS STEPS SAVE_EVERY; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
        echo "ERROR: $value_name must be a positive integer; received '$value'." >&2
        exit 2
    fi
done
if (( STEPS % SAVE_EVERY )); then
    echo "ERROR: SAVE_EVERY must divide STEPS exactly." >&2
    exit 2
fi
if [[ "$ROOT" != /* || "$ROOT" == / ]]; then
    echo "ERROR: ROOT must be a non-root absolute path." >&2
    exit 2
fi

if [[ -z "${MFC_ROOT:-}" ]]; then
    if [[ -x "$ROOT/third_party/MFC-0c9a1d43/mfc.sh" ]]; then
        MFC_ROOT="$ROOT/third_party/MFC-0c9a1d43"
    fi
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
        echo "A grid study must not change solver versions between levels." >&2
        exit 2
    fi
else
    actual_mfc_commit="directory-name-pinned-0c9a1d43"
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_BASE="$ROOT/mfc_runs/fixed_ib_a40_f405_jfm_${STAMP}"
CASE_DIR="$RUN_BASE/f405_t13p5"
mkdir -p "$CASE_DIR"

# Companion files are pinned to the first reviewed commit containing this
# workflow.  The submitter may advance later without changing the scientific
# inputs used by an already submitted run.
RAW_BASE="https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/c6c8b3f62da42ffe1d3318a7cf7c6a5d6b2a1c2c/mfc_grid_convergence"
curl -fL --retry 3 "$RAW_BASE/case_f405.py" -o "$CASE_DIR/case.py"
curl -fL --retry 3 "$RAW_BASE/Diamond_Airfoil_2D_MFC.stl" \
    -o "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl"

if [[ "$(grep -c 'facet normal' "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl")" -ne 2 ]]; then
    echo "ERROR: expected the validated two-triangle planar STL." >&2
    exit 2
fi

python3 - "$CASE_DIR/case.py" "$STEPS" "$SAVE_EVERY" <<'PY'
import json
import math
import subprocess
import sys

case_path, steps, save_every = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
raw = subprocess.check_output(
    [sys.executable, case_path, "--steps", str(steps), "--save-every", str(save_every)],
    text=True,
)
case = json.loads(raw)
expected = {
    "m": 4454,
    "n": 4049,
    "t_step_stop": steps,
    "t_step_save": save_every,
    "ib": "T",
    "patch_ib(1)%slip": "T",
    "viscous": "F",
    "precision": "double",
}
for key, value in expected.items():
    if case.get(key) != value:
        raise SystemExit(f"ERROR: {key}={case.get(key)!r}; expected {value!r}")
if not math.isclose(case["dt"], 1.0 / 8100.0, rel_tol=0.0, abs_tol=1.0e-15):
    raise SystemExit(f"ERROR: unexpected dt={case['dt']!r}")
print("CASE_PREFLIGHT=PASS")
print(f"GRID={(case['m'] + 1)}x{(case['n'] + 1)}")
print(f"DT={case['dt']:.16g}")
print(f"T_STOP={steps * case['dt']:.16g}")
print(f"SAVE_DT={save_every * case['dt']:.16g}")
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
        AFTER_JOB="none"
    fi
fi

dependency_args=()
if [[ -n "$AFTER_JOB" && "$AFTER_JOB" != none ]]; then
    if [[ ! "$AFTER_JOB" =~ ^[0-9]+(:[0-9]+)*$ ]]; then
        echo "ERROR: AFTER_JOB must be auto, none, or colon-separated job IDs." >&2
        exit 2
    fi
    dependency_args+=("--dependency=afterany:${AFTER_JOB}")
fi

SBATCH_FILE="$CASE_DIR/run_f405.sbatch"
cat >"$SBATCH_FILE" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1
#SBATCH --job-name=mfc-a40-f405
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err

set -Eeuo pipefail

: "${CASE_DIR:?}"
: "${MFC_ROOT:?}"
: "${STEPS:?}"
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

cd "$MFC_ROOT"
mkdir -p build

# Share the established startup lock during this read-only --no-build run.
# A builder that needs the exclusive lock cannot modify MFC while f405 is
# running.  The Slurm dependency normally means the current continuation has
# already released its exclusive lock before this job starts.
LOCK_FILE="$MFC_ROOT/build/.mfc-a40-startup.lock"
if command -v flock >/dev/null 2>&1 && [[ "${MFC_LOCK_HELD:-0}" != 1 ]]; then
    export MFC_LOCK_HELD=1
    echo "Waiting for shared MFC runtime lock: $LOCK_FILE"
    exec flock -s -w 7200 "$LOCK_FILE" bash "$0"
fi

./mfc.sh validate "$CASE_DIR/case.py" \
    -- --steps "$STEPS" --save-every "$SAVE_EVERY" \
    2>&1 | tee "$CASE_DIR/validate-f405.log"

LOG="$CASE_DIR/mfc-f405.log"
set +e
./mfc.sh run "$CASE_DIR/case.py" \
    -n "$SLURM_NTASKS" -j 1 --mpi --no-gpu --binary mpirun --no-build \
    -t pre_process simulation post_process -- \
    --steps "$STEPS" --save-every "$SAVE_EVERY" \
    2>&1 | tee "$LOG"
mfc_status=${PIPESTATUS[0]}
set -e

if [[ "$mfc_status" -ne 0 ]]; then
    echo "MFC f405 failed with exit code $mfc_status" >&2
    exit "$mfc_status"
fi

grep -am1 'Number of 2D model boundary edges' "$LOG"
grep -aq 'Number of 2D model boundary edges: *4' "$LOG" || {
    echo "ERROR: MFC did not detect four airfoil boundary edges." >&2
    exit 3
}
[[ -s "$CASE_DIR/restart_data/lustre_${STEPS}.dat" ]] || {
    echo "ERROR: final restart step $STEPS was not written." >&2
    exit 3
}

touch "$CASE_DIR/RUN_OK_F405.txt"
echo "RUN_OK_F405=$CASE_DIR/RUN_OK_F405.txt"
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
write_env STEPS "$STEPS"
write_env SAVE_EVERY "$SAVE_EVERY"
write_env NTASKS "$NTASKS"
write_env MEMORY "$MEMORY"
write_env WALLTIME "$WALLTIME"
write_env QOS "$QOS"
write_env CONSTRAINT "$CONSTRAINT"
write_env AFTER_JOB "$AFTER_JOB"

sbatch_args=(
    --parsable
    --ntasks="$NTASKS"
    --mem="$MEMORY"
    --time="$WALLTIME"
    --qos="$QOS"
    --constraint="$CONSTRAINT"
    --export="ALL,CASE_DIR=$CASE_DIR,MFC_ROOT=$MFC_ROOT,STEPS=$STEPS,SAVE_EVERY=$SAVE_EVERY"
)
sbatch_args+=("${dependency_args[@]}")

(
    cd "$CASE_DIR"
    JOB_ID="$(sbatch "${sbatch_args[@]}" "$SBATCH_FILE")"
    echo "JOB_ID=$JOB_ID" | tee -a "$ENV_FILE"
)

echo "RUN_BASE=$RUN_BASE"
echo "CASE_DIR=$CASE_DIR"
echo "ENV_FILE=$ENV_FILE"
echo "AFTER_JOB=$AFTER_JOB"
grep '^JOB_ID=' "$ENV_FILE"
echo "STATUS: source '$ENV_FILE'; squeue -j \"\$JOB_ID\""
