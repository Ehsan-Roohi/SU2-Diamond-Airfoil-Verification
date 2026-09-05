#!/usr/bin/env bash
set -Eeuo pipefail

# Submit the official MFC Re_D=40, Ma=0.1 viscous-cylinder example on Unity.
# Gate mode changes only t_stop/t_save; all physics, grid, IBM and numerics are
# copied byte-for-byte from MFC commit 0c9a1d43 before those two substitutions.

REPOSITORY="Ehsan-Roohi/SU2-Diamond-Airfoil-Verification"
SOURCE_REF="agent/mfc-euler-cylinder-validation"
MFC_REF="0c9a1d434410175ac483b8d71646455444e3b7eb"
OFFICIAL_BLOB_SHA="6bb12ec0ee5e0fecdce30d0a28f3a60e8bc5f001"
OFFICIAL_REL="examples/2D_ibm_viscous_drag_over_cylinder/case.py"

DEFAULT_SCRATCH=/scratch4/workspace/roohie_umass_edu-mfc-a40-cv
DEFAULT_PROJECT=/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data
MFC_ROOT="${MFC_ROOT:-$DEFAULT_SCRATCH/MFC-0c9a1d43-viscous-cylinder-v1}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$DEFAULT_PROJECT/runs/mfc_official_re40_cylinder}"
RUN_MODE="${RUN_MODE:-gate}"
NTASKS="${NTASKS:-32}"
MEMORY="${MEMORY:-220G}"
RUN_SET_ID="${RUN_SET_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"

case "$RUN_MODE" in
    gate)
        T_STOP="${T_STOP:-1.0}"
        T_SAVE="${T_SAVE:-0.25}"
        TIME_LIMIT="${TIME_LIMIT:-12:00:00}"
        ;;
    full)
        T_STOP="${T_STOP:-100.0}"
        T_SAVE="${T_SAVE:-10.0}"
        TIME_LIMIT="${TIME_LIMIT:-3-00:00:00}"
        ;;
    *)
        echo "ERROR: RUN_MODE must be gate or full." >&2
        exit 2
        ;;
esac

for path in "$MFC_ROOT" "$OUTPUT_ROOT"; do
    [[ "$path" == /* && "$path" != / ]] || {
        echo "ERROR: paths must be non-root absolute paths: $path" >&2
        exit 2
    }
done
[[ -x "$MFC_ROOT/mfc.sh" ]] || {
    echo "ERROR: MFC installation not found: $MFC_ROOT" >&2
    exit 3
}

OFFICIAL_CASE="$MFC_ROOT/$OFFICIAL_REL"
[[ -s "$OFFICIAL_CASE" ]] || {
    echo "ERROR: official MFC example is missing: $OFFICIAL_CASE" >&2
    exit 3
}
ACTUAL_BLOB_SHA="$(git hash-object "$OFFICIAL_CASE")"
[[ "$ACTUAL_BLOB_SHA" == "$OFFICIAL_BLOB_SHA" ]] || {
    echo "ERROR: official case does not match pinned MFC commit $MFC_REF" >&2
    echo "expected_blob=$OFFICIAL_BLOB_SHA actual_blob=$ACTUAL_BLOB_SHA" >&2
    exit 4
}

RUN_BASE="$OUTPUT_ROOT/re40_ma0p1_${RUN_MODE}_$RUN_SET_ID"
[[ ! -e "$RUN_BASE" ]] || {
    echo "ERROR: run directory already exists: $RUN_BASE" >&2
    exit 4
}
CASE_DIR="$RUN_BASE/case"
mkdir -p "$CASE_DIR"
cp "$OFFICIAL_CASE" "$CASE_DIR/OFFICIAL_CASE_UNMODIFIED.py"

python3 - "$OFFICIAL_CASE" "$CASE_DIR/case.py" "$T_STOP" "$T_SAVE" <<'PY'
import math
import sys
from pathlib import Path

source, target = map(Path, sys.argv[1:3])
t_stop, t_save = map(float, sys.argv[3:5])
if not (math.isfinite(t_stop) and math.isfinite(t_save)):
    raise SystemExit("ERROR: T_STOP and T_SAVE must be finite")
if not (0.0 < t_save <= t_stop <= 100.0):
    raise SystemExit("ERROR: require 0 < T_SAVE <= T_STOP <= 100")

text = source.read_text(encoding="utf-8")
old_save = '"t_save": 10.0,'
old_stop = '"t_stop": 100.0,'
if text.count(old_save) != 1 or text.count(old_stop) != 1:
    raise SystemExit("ERROR: official time-control lines were not uniquely found")
text = text.replace(old_save, f'"t_save": {t_save!r},')
text = text.replace(old_stop, f'"t_stop": {t_stop!r},')
target.write_text(text, encoding="utf-8")
PY

cat >"$CASE_DIR/PROVENANCE.env" <<EOF
status=READY
source_repository=MFlowCode/MFC
source_commit=$MFC_REF
source_path=$OFFICIAL_REL
source_git_blob=$OFFICIAL_BLOB_SHA
run_mode=$RUN_MODE
mach=0.1
reynolds_diameter=40
cylinder_cells_per_diameter=100
grid_m=3000
grid_n=3000
cfl_adaptive=true
cfl_target=0.5
t_stop=$T_STOP
t_save=$T_SAVE
EOF

SBATCH_FILE="$RUN_BASE/run.sbatch"
cat >"$SBATCH_FILE" <<'SBATCH'
#!/usr/bin/env bash
set -Eeuo pipefail

: "${CASE_DIR:?}" "${MFC_ROOT:?}" "${RUN_MODE:?}"
: "${T_STOP:?}" "${T_SAVE:?}" "${MFC_REF:?}"

failure_marker="$CASE_DIR/RUN_FAILED_OFFICIAL_MFC_RE40.txt"
success_marker="$CASE_DIR/RUN_OK_OFFICIAL_MFC_RE40.txt"
trap 'rc=$?; { printf "status=FAILED\n"; printf "exit_code=%s\n" "$rc"; printf "scope=%s\n" "$RUN_MODE"; } >"$failure_marker"; exit "$rc"' ERR
rm -f "$failure_marker" "$success_marker"

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1

mkdir -p "$MFC_ROOT/build"
LOCK_FILE="$MFC_ROOT/build/.mfc-official-re40.lock"
if command -v flock >/dev/null 2>&1 && [[ "${MFC_LOCK_HELD:-0}" != 1 ]]; then
    export MFC_LOCK_HELD=1
    exec flock -x -w 7200 "$LOCK_FILE" bash "$0"
fi

cd "$MFC_ROOT"
./mfc.sh validate "$CASE_DIR/case.py" \
    2>&1 | tee "$CASE_DIR/validate.log"
./mfc.sh run "$CASE_DIR/case.py" \
    -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" \
    --mpi --no-gpu --binary mpirun --scratch \
    -t pre_process simulation \
    2>&1 | tee "$CASE_DIR/simulation.log"

STATE_COUNT="$(find "$CASE_DIR/restart_data" -maxdepth 1 -type f -name 'lustre_[0-9]*.dat' -size +0c 2>/dev/null | wc -l)"
IB_COUNT="$(find "$CASE_DIR/restart_data" -maxdepth 1 -type f -name 'ib_state_[0-9]*.dat' -size +0c 2>/dev/null | wc -l)"
(( STATE_COUNT >= 2 )) || {
    echo "ERROR: fewer than two nonempty flow states were produced." >&2
    exit 5
}

{
    printf 'status=NUMERICAL_EXECUTION_PASS\n'
    printf 'physical_validation=NOT_YET_CLAIMED\n'
    printf 'source_commit=%s\n' "$MFC_REF"
    printf 'run_mode=%s\n' "$RUN_MODE"
    printf 'mach=0.1\nreynolds_diameter=40\n'
    printf 't_stop=%s\nt_save=%s\n' "$T_STOP" "$T_SAVE"
    printf 'flow_states=%s\nib_states=%s\n' "$STATE_COUNT" "$IB_COUNT"
} | tee "$success_marker"
SBATCH

JOB_ID="$(sbatch --parsable \
    --partition=cpu \
    --nodes=1 \
    --ntasks="$NTASKS" \
    --cpus-per-task=1 \
    --mem="$MEMORY" \
    --time="$TIME_LIMIT" \
    --constraint='intel&x86_64_v4' \
    --job-name="mfc-official-re40-$RUN_MODE" \
    --output="$RUN_BASE/slurm-%j.out" \
    --error="$RUN_BASE/slurm-%j.err" \
    --export="ALL,CASE_DIR=$CASE_DIR,MFC_ROOT=$MFC_ROOT,RUN_MODE=$RUN_MODE,T_STOP=$T_STOP,T_SAVE=$T_SAVE,MFC_REF=$MFC_REF" \
    "$SBATCH_FILE")"
JOB_ID="${JOB_ID%%;*}"

ENV_FILE="$RUN_BASE/submission.env"
{
    printf 'STATUS=%q\n' SUBMITTED
    printf 'SOURCE_REPOSITORY=%q\n' "$REPOSITORY"
    printf 'SOURCE_REF=%q\n' "$SOURCE_REF"
    printf 'MFC_REF=%q\n' "$MFC_REF"
    printf 'RUN_MODE=%q\n' "$RUN_MODE"
    printf 'RUN_BASE=%q\n' "$RUN_BASE"
    printf 'CASE_DIR=%q\n' "$CASE_DIR"
    printf 'JOB_ID=%q\n' "$JOB_ID"
    printf 'T_STOP=%q\n' "$T_STOP"
    printf 'T_SAVE=%q\n' "$T_SAVE"
} >"$ENV_FILE"

echo "MFC_OFFICIAL_RE40_SUBMITTED=PASS"
echo "RUN_MODE=$RUN_MODE"
echo "RUN_BASE=$RUN_BASE"
echo "CASE_DIR=$CASE_DIR"
echo "JOB_ID=$JOB_ID"
echo "ENV_FILE=$ENV_FILE"
echo "WATCH=squeue -j $JOB_ID"
echo "CHECK=sacct -j $JOB_ID -X --format=JobID,JobName,State,Elapsed,ExitCode,MaxRSS,NodeList"
