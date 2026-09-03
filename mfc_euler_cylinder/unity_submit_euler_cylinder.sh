#!/usr/bin/env bash
set -Eeuo pipefail

# One-command Unity launcher for the continuum, inviscid circular-cylinder
# bow-shock case.  Default production run: Mach 2.7 on the f90 pilot grid.

REPOSITORY="Ehsan-Roohi/SU2-Diamond-Airfoil-Verification"
SOURCE_REF="agent/mfc-euler-cylinder-validation"
RAW_BASE="https://raw.githubusercontent.com/$REPOSITORY/$SOURCE_REF/mfc_euler_cylinder"

DEFAULT_ROOT=/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification
DEFAULT_DATA_ROOT=/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data
ROOT="${ROOT:-$DEFAULT_ROOT}"
DATA_ROOT="${DATA_ROOT:-$DEFAULT_DATA_ROOT}"
MACH="${MACH:-2.7}"
GRID="${GRID:-f90}"
FINAL_TIME="${FINAL_TIME:-3.0}"
SAVE_DT="${SAVE_DT:-0.1}"
AFTER_JOB="${AFTER_JOB:-none}"
EXPECTED_MFC_COMMIT=0c9a1d434410175ac483b8d71646455444e3b7eb
MFC_SOURCE_ROOT="${MFC_SOURCE_ROOT:-$ROOT/third_party/MFC-0c9a1d43}"
MFC_CYL_ROOT="${MFC_CYL_ROOT:-$ROOT/third_party/MFC-0c9a1d43-euler-cylinder-portable-v3}"

case "$GRID" in
    f90)  PROD_NTASKS=16; PROD_MEMORY=48G;  PROD_WALLTIME=12:00:00 ;;
    f180) PROD_NTASKS=24; PROD_MEMORY=80G;  PROD_WALLTIME=1-00:00:00 ;;
    f270) PROD_NTASKS=32; PROD_MEMORY=112G; PROD_WALLTIME=1-18:00:00 ;;
    *) echo "ERROR: GRID must be f90, f180, or f270; received '$GRID'." >&2; exit 2 ;;
esac

if [[ "$ROOT" != /* || "$ROOT" == / || "$DATA_ROOT" != /* || "$DATA_ROOT" == / ]]; then
    echo "ERROR: ROOT and DATA_ROOT must be non-root absolute paths." >&2
    exit 2
fi
if [[ ! "$MACH" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "ERROR: MACH must be a positive decimal number." >&2
    exit 2
fi
python3 - "$MACH" <<'PY'
import sys
mach = float(sys.argv[1])
if mach <= 1.0:
    raise SystemExit("ERROR: MACH must be supersonic")
PY
if [[ ! -x "$MFC_SOURCE_ROOT/mfc.sh" ]]; then
    echo "ERROR: pinned source MFC checkout not found: $MFC_SOURCE_ROOT" >&2
    exit 2
fi
if [[ "$MFC_CYL_ROOT" == "$MFC_SOURCE_ROOT" ]]; then
    echo "ERROR: MFC_CYL_ROOT must differ from the preserved source checkout." >&2
    exit 2
fi
if [[ -n "$AFTER_JOB" && "$AFTER_JOB" != none && ! "$AFTER_JOB" =~ ^[0-9]+(:[0-9]+)*$ ]]; then
    echo "ERROR: AFTER_JOB must be none or colon-separated Slurm job IDs." >&2
    exit 2
fi

# MFC specializes enabled features at build time.  Preserve the existing MFC
# trees and use an isolated, reproducibly pinned build for this geometry.
SOURCE_SYNC_MARKER="$MFC_CYL_ROOT/.unity-source-sync-$EXPECTED_MFC_COMMIT"
if [[ ! -s "$SOURCE_SYNC_MARKER" || ! -x "$MFC_CYL_ROOT/mfc.sh" ]]; then
    command -v rsync >/dev/null || { echo "ERROR: rsync is required." >&2; exit 2; }
    mkdir -p "$MFC_CYL_ROOT"
    rsync -a --exclude=/build/ --exclude=/install/ --exclude=/run/ \
        "$MFC_SOURCE_ROOT/" "$MFC_CYL_ROOT/"
    [[ -x "$MFC_CYL_ROOT/mfc.sh" && -s "$MFC_CYL_ROOT/cmake/GPU.cmake" ]] || {
        echo "ERROR: isolated MFC tree is incomplete: $MFC_CYL_ROOT" >&2
        exit 2
    }
    printf '%s\n' "$EXPECTED_MFC_COMMIT" >"$SOURCE_SYNC_MARKER"
fi

if git -C "$MFC_CYL_ROOT" rev-parse HEAD >/dev/null 2>&1; then
    actual_mfc_commit="$(git -C "$MFC_CYL_ROOT" rev-parse HEAD)"
    [[ "$actual_mfc_commit" == "$EXPECTED_MFC_COMMIT" ]] || {
        echo "ERROR: isolated MFC commit is $actual_mfc_commit; expected $EXPECTED_MFC_COMMIT." >&2
        exit 2
    }
else
    actual_mfc_commit=directory-name-pinned-0c9a1d43
fi

# Avoid binaries tied to one Unity CPU family.
PORTABLE_ARCH=x86-64-v3
GPU_CMAKE="$MFC_CYL_ROOT/cmake/GPU.cmake"
python3 - "$GPU_CMAKE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
replacements = (
    ('CHECK_FORTRAN_COMPILER_FLAG("-march=native"',
     'CHECK_FORTRAN_COMPILER_FLAG("-march=x86-64-v3"'),
    ('COMPILE_LANGUAGE:Fortran>:-march=native',
     'COMPILE_LANGUAGE:Fortran>:-march=x86-64-v3'),
    ('CHECK_FORTRAN_COMPILER_FLAG("-mcpu=native"',
     'CHECK_FORTRAN_COMPILER_FLAG("-mtune=generic"'),
    ('COMPILE_LANGUAGE:Fortran>:-mcpu=native',
     'COMPILE_LANGUAGE:Fortran>:-mtune=generic'),
)
for native, portable in replacements:
    native_count = text.count(native)
    portable_count = text.count(portable)
    if native_count == 1 and portable_count == 0:
        text = text.replace(native, portable)
    elif native_count == 0 and portable_count == 1:
        pass
    else:
        raise SystemExit(
            f"ERROR: unexpected compiler flag state for {native}: "
            f"native={native_count}, portable={portable_count}"
        )
for forbidden in (
    "COMPILE_LANGUAGE:Fortran>:-march=native",
    "COMPILE_LANGUAGE:Fortran>:-mcpu=native",
):
    if forbidden in text:
        raise SystemExit("ERROR: native CPU tuning remains in the isolated MFC tree")
path.write_text(text)
PY
PORTABLE_CMAKE_SHA256="$(sha256sum "$GPU_CMAKE" | awk '{print $1}')"

STAMP="$(date +%Y%m%d-%H%M%S)"
MACH_TAG="${MACH/./p}"
RUN_BASE="$DATA_ROOT/runs/mfc_euler_cylinder/m${MACH_TAG}_${GRID}_${STAMP}"
SMOKE_DIR="$RUN_BASE/smoke"
CASE_DIR="$RUN_BASE/$GRID"
mkdir -p "$SMOKE_DIR" "$CASE_DIR"

for destination in "$SMOKE_DIR" "$CASE_DIR"; do
    curl -fL --retry 3 "$RAW_BASE/case.py" -o "$destination/case.py"
    curl -fL --retry 3 "$RAW_BASE/rankine_hugoniot_reference.py" \
        -o "$destination/rankine_hugoniot_reference.py"
    curl -fL --retry 3 "$RAW_BASE/validation_protocol.json" \
        -o "$destination/validation_protocol.json"
done

python3 - "$CASE_DIR/case.py" "$MACH" "$GRID" "$FINAL_TIME" "$SAVE_DT" <<'PY'
import json
import math
import subprocess
import sys

path, mach, grid, final_time, save_dt = sys.argv[1:]
raw = subprocess.check_output(
    [sys.executable, path, "--mach", mach, "--grid", grid,
     "--final-time", final_time, "--save-dt", save_dt],
    text=True,
)
case = json.loads(raw)
expected_indices = {
    "f90": (989, 899), "f180": (1979, 1799), "f270": (2969, 2699)
}
for key, value in {
    "m": expected_indices[grid][0],
    "n": expected_indices[grid][1],
    "p": 0,
    "model_eqns": 2,
    "viscous": "F",
    "patch_ib(1)%geometry": 2,
    "patch_ib(1)%radius": 0.5,
    "patch_ib(1)%slip": "T",
    "riemann_solver": 2,
    "weno_order": 5,
    "bc_x%beg": -11,
    "bc_x%end": -12,
}.items():
    if case.get(key) != value:
        raise SystemExit(f"ERROR: {key}={case.get(key)!r}; expected {value!r}")
if any(key.startswith("fluid_pp(1)%Re") for key in case):
    raise SystemExit("ERROR: Euler case must not define viscosity/Re parameters")
if not math.isclose(case["patch_icpp(1)%vel(1)"], float(mach), abs_tol=1e-14):
    raise SystemExit("ERROR: nondimensional freestream velocity must equal Mach")
expected_snapshots = case["t_step_stop"] // case["t_step_save"] + 1
if expected_snapshots != 31:
    raise SystemExit(f"ERROR: expected 31 production snapshots, got {expected_snapshots}")
print(f"PREFLIGHT=PASS MACH={mach} GRID={grid} SNAPSHOTS={expected_snapshots}")
PY

python3 "$CASE_DIR/rankine_hugoniot_reference.py" --mach "$MACH" \
    --output "$CASE_DIR/RH_REFERENCE.json"
CASE_SHA256="$(sha256sum "$CASE_DIR/case.py" | awk '{print $1}')"

SBATCH_FILE="$RUN_BASE/run_mfc_euler_cylinder.sbatch"
cat >"$SBATCH_FILE" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1

set -Eeuo pipefail
: "${CASE_DIR:?}"
: "${MFC_CYL_ROOT:?}"
: "${MACH:?}"
: "${GRID:?}"
: "${FINAL_TIME:?}"
: "${SAVE_DT:?}"
: "${BUILD_MODE:?}"
: "${EXPECTED_SNAPSHOTS:?}"
: "${PORTABLE_CMAKE_SHA256:?}"

GPU_CMAKE="$MFC_CYL_ROOT/cmake/GPU.cmake"
[[ "$(sha256sum "$GPU_CMAKE" | awk '{print $1}')" == "$PORTABLE_CMAKE_SHA256" ]] || {
    echo "ERROR: portable compiler configuration changed after submission." >&2
    exit 5
}

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
command -v mpirun >/dev/null || { echo "ERROR: mpirun not found." >&2; exit 5; }

CASE_ARGS=(--mach "$MACH" --grid "$GRID" --final-time "$FINAL_TIME" --save-dt "$SAVE_DT")
cd "$MFC_CYL_ROOT"
mkdir -p build
LOCK_FILE="$MFC_CYL_ROOT/build/.mfc-euler-cylinder.lock"
if command -v flock >/dev/null 2>&1 && [[ "${MFC_LOCK_HELD:-0}" != 1 ]]; then
    export MFC_LOCK_HELD=1
    if [[ "$BUILD_MODE" == scratch ]]; then
        exec flock -x -w 7200 "$LOCK_FILE" bash "$0"
    else
        exec flock -s -w 7200 "$LOCK_FILE" bash "$0"
    fi
fi

./mfc.sh validate "$CASE_DIR/case.py" -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/validate-${GRID}.log"

build_args=(--no-build)
if [[ "$BUILD_MODE" == scratch ]]; then
    build_args=(--scratch)
fi
LOG="$CASE_DIR/mfc-${GRID}.log"
set +e
./mfc.sh run "$CASE_DIR/case.py" \
    -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" --mpi --no-gpu --binary mpirun \
    "${build_args[@]}" -t pre_process simulation post_process -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$LOG"
mfc_status=${PIPESTATUS[0]}
set -e
(( mfc_status == 0 )) || exit "$mfc_status"

STOP_STEP="$(python3 "$CASE_DIR/case.py" "${CASE_ARGS[@]}" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["t_step_stop"])')"
SAVE_EVERY="$(python3 "$CASE_DIR/case.py" "${CASE_ARGS[@]}" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["t_step_save"])')"
FINAL_RESTART="$CASE_DIR/restart_data/lustre_${STOP_STEP}.dat"
FINAL_SILO="$CASE_DIR/silo_hdf5/root/collection_${STOP_STEP}.silo"
[[ -s "$FINAL_RESTART" ]] || { echo "ERROR: missing $FINAL_RESTART" >&2; exit 41; }
[[ -s "$FINAL_SILO" ]] || { echo "ERROR: missing $FINAL_SILO" >&2; exit 42; }

restart_count="$(find "$CASE_DIR/restart_data" -maxdepth 1 -type f -name 'lustre_[0-9]*.dat' | wc -l)"
silo_count="$(find "$CASE_DIR/silo_hdf5/root" -maxdepth 1 -type f -name 'collection_*.silo' | wc -l)"
if (( restart_count < EXPECTED_SNAPSHOTS || silo_count < EXPECTED_SNAPSHOTS )); then
    echo "ERROR: expected $EXPECTED_SNAPSHOTS states; restart=$restart_count silo=$silo_count" >&2
    exit 43
fi

INVENTORY="$CASE_DIR/FIELD_INVENTORY.tsv"
printf 'step\ttime\trestart_bytes\tsilo_bytes\n' >"$INVENTORY"
DT="$(python3 "$CASE_DIR/case.py" "${CASE_ARGS[@]}" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["dt"])')"
for (( step=0; step<=STOP_STEP; step+=SAVE_EVERY )); do
    restart="$CASE_DIR/restart_data/lustre_${step}.dat"
    silo="$CASE_DIR/silo_hdf5/root/collection_${step}.silo"
    [[ -s "$restart" && -s "$silo" ]] || { echo "ERROR: incomplete state $step" >&2; exit 44; }
    time_value="$(python3 -c "print(${step} * ${DT})")"
    printf '%s\t%s\t%s\t%s\n' "$step" "$time_value" \
        "$(stat -c %s "$restart")" "$(stat -c %s "$silo")" >>"$INVENTORY"
done

printf 'status=PASS\nmach=%s\ngrid=%s\nfinal_time=%s\nsnapshots=%s\nfinal_restart=%s\nfinal_silo=%s\n' \
    "$MACH" "$GRID" "$(python3 -c "print(${STOP_STEP} * ${DT})")" \
    "$EXPECTED_SNAPSHOTS" "$FINAL_RESTART" "$FINAL_SILO" | \
    tee "$CASE_DIR/RUN_OK_MFC_EULER_CYLINDER.txt"
SBATCH

ENV_FILE="$RUN_BASE/submission.env"
{
    printf 'RUN_BASE=%q\n' "$RUN_BASE"
    printf 'CASE_DIR=%q\n' "$CASE_DIR"
    printf 'SMOKE_DIR=%q\n' "$SMOKE_DIR"
    printf 'MFC_CYL_ROOT=%q\n' "$MFC_CYL_ROOT"
    printf 'MACH=%q\n' "$MACH"
    printf 'GRID=%q\n' "$GRID"
    printf 'FINAL_TIME=%q\n' "$FINAL_TIME"
    printf 'SAVE_DT=%q\n' "$SAVE_DT"
    printf 'CASE_SHA256=%q\n' "$CASE_SHA256"
    printf 'MFC_COMMIT=%q\n' "$actual_mfc_commit"
    printf 'PORTABLE_CMAKE_SHA256=%q\n' "$PORTABLE_CMAKE_SHA256"
} >"$ENV_FILE"

dependency_args=()
if [[ -n "$AFTER_JOB" && "$AFTER_JOB" != none ]]; then
    dependency_args+=(--dependency="afterok:$AFTER_JOB")
fi

SMOKE_JOB="$({
    cd "$SMOKE_DIR"
    sbatch --parsable --ntasks=8 --mem=32G --time=02:00:00 \
        --constraint='intel&x86_64_v4' "${dependency_args[@]}" \
        --job-name=mfc-euler-cyl-smoke \
        --output="$SMOKE_DIR/slurm-%j.out" --error="$SMOKE_DIR/slurm-%j.err" \
        --export="ALL,CASE_DIR=$SMOKE_DIR,MFC_CYL_ROOT=$MFC_CYL_ROOT,MACH=$MACH,GRID=smoke,FINAL_TIME=0.05,SAVE_DT=0.025,BUILD_MODE=scratch,EXPECTED_SNAPSHOTS=3,PORTABLE_CMAKE_SHA256=$PORTABLE_CMAKE_SHA256" \
        "$SBATCH_FILE"
})"
SMOKE_JOB="${SMOKE_JOB%%;*}"
[[ "$SMOKE_JOB" =~ ^[0-9]+$ ]] || { echo "ERROR: invalid smoke job ID '$SMOKE_JOB'" >&2; exit 4; }

PROD_JOB="$({
    cd "$CASE_DIR"
    sbatch --parsable --ntasks="$PROD_NTASKS" --mem="$PROD_MEMORY" --time="$PROD_WALLTIME" \
        --constraint='intel&x86_64_v4' --dependency="afterok:$SMOKE_JOB" \
        --job-name="mfc-euler-cyl-m${MACH_TAG}-${GRID}" \
        --output="$CASE_DIR/slurm-%j.out" --error="$CASE_DIR/slurm-%j.err" \
        --export="ALL,CASE_DIR=$CASE_DIR,MFC_CYL_ROOT=$MFC_CYL_ROOT,MACH=$MACH,GRID=$GRID,FINAL_TIME=$FINAL_TIME,SAVE_DT=$SAVE_DT,BUILD_MODE=nobuild,EXPECTED_SNAPSHOTS=31,PORTABLE_CMAKE_SHA256=$PORTABLE_CMAKE_SHA256" \
        "$SBATCH_FILE"
})"
PROD_JOB="${PROD_JOB%%;*}"
if [[ ! "$PROD_JOB" =~ ^[0-9]+$ ]]; then
    scancel "$SMOKE_JOB" || true
    echo "ERROR: invalid production job ID '$PROD_JOB'" >&2
    exit 4
fi

printf 'SMOKE_JOB=%q\nPROD_JOB=%q\nJOBS=%q\n' \
    "$SMOKE_JOB" "$PROD_JOB" "$SMOKE_JOB,$PROD_JOB" >>"$ENV_FILE"

echo "RUN_BASE=$RUN_BASE"
echo "CASE_DIR=$CASE_DIR"
echo "ENV_FILE=$ENV_FILE"
echo "SMOKE_JOB=$SMOKE_JOB"
echo "PROD_JOB=$PROD_JOB"
echo "NEXT: source '$ENV_FILE'; squeue -j \"\$JOBS\""
