#!/usr/bin/env bash
set -Eeuo pipefail

# One-command Unity launcher for the Mach-3/AoA-40 viscous no-model screen.
# Default: f270, t=0..3, 61 permanent physical snapshots at Delta(t)=0.05.

REPOSITORY="Ehsan-Roohi/SU2-Diamond-Airfoil-Verification"
SOURCE_REF="agent/mfc-a40-iles-screen"
RAW_BASE="https://raw.githubusercontent.com/$REPOSITORY/$SOURCE_REF/mfc_iles_a40"

DEFAULT_ROOT=/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification
DEFAULT_DATA_ROOT=/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data
ROOT="${ROOT:-$DEFAULT_ROOT}"
DATA_ROOT="${DATA_ROOT:-$DEFAULT_DATA_ROOT}"
GRID="${GRID:-f270}"
FINAL_TIME="${FINAL_TIME:-3.0}"
SAVE_DT="${SAVE_DT:-0.05}"
AFTER_JOB="${AFTER_JOB:-none}"
EXPECTED_MFC_COMMIT=0c9a1d434410175ac483b8d71646455444e3b7eb
MFC_SOURCE_ROOT="${MFC_SOURCE_ROOT:-$ROOT/third_party/MFC-0c9a1d43}"
MFC_ILES_ROOT="${MFC_ILES_ROOT:-$ROOT/third_party/MFC-0c9a1d43-iles-portable-v3}"

case "$GRID" in
    f180) PROD_NTASKS=24; PROD_MEMORY=64G;  PROD_WALLTIME=20:00:00 ;;
    f270) PROD_NTASKS=32; PROD_MEMORY=96G;  PROD_WALLTIME=1-12:00:00 ;;
    f405) PROD_NTASKS=48; PROD_MEMORY=160G; PROD_WALLTIME=2-00:00:00 ;;
    *) echo "ERROR: GRID must be f180, f270, or f405; received '$GRID'." >&2; exit 2 ;;
esac

if [[ "$ROOT" != /* || "$ROOT" == / || "$DATA_ROOT" != /* || "$DATA_ROOT" == / ]]; then
    echo "ERROR: ROOT and DATA_ROOT must be non-root absolute paths." >&2
    exit 2
fi
if [[ "$MFC_ILES_ROOT" == "$MFC_SOURCE_ROOT" ]]; then
    echo "ERROR: MFC_ILES_ROOT must differ from the Euler MFC checkout." >&2
    exit 2
fi
if [[ ! -x "$MFC_SOURCE_ROOT/mfc.sh" ]]; then
    echo "ERROR: pinned source MFC checkout not found: $MFC_SOURCE_ROOT" >&2
    exit 2
fi
if [[ -n "$AFTER_JOB" && "$AFTER_JOB" != none && ! "$AFTER_JOB" =~ ^[0-9]+(:[0-9]+)*$ ]]; then
    echo "ERROR: AFTER_JOB must be none or colon-separated Slurm job IDs." >&2
    exit 2
fi

# Do not rebuild the Euler checkout: MFC uses compile-time case optimization.
# Make a separate source/build tree for the viscous configuration.
if [[ ! -x "$MFC_ILES_ROOT/mfc.sh" ]]; then
    command -v rsync >/dev/null || {
        echo "ERROR: rsync is required to prepare the isolated MFC build." >&2
        exit 2
    }
    [[ ! -e "$MFC_ILES_ROOT" ]] || {
        echo "ERROR: incomplete MFC_ILES_ROOT already exists: $MFC_ILES_ROOT" >&2
        exit 2
    }
    mkdir -p "$MFC_ILES_ROOT"
    rsync -a --exclude=/build/ --exclude=/install/ --exclude=/run/ \
        "$MFC_SOURCE_ROOT/" "$MFC_ILES_ROOT/"
fi

if git -C "$MFC_ILES_ROOT" rev-parse HEAD >/dev/null 2>&1; then
    actual_mfc_commit="$(git -C "$MFC_ILES_ROOT" rev-parse HEAD)"
    if [[ "$actual_mfc_commit" != "$EXPECTED_MFC_COMMIT" ]]; then
        echo "ERROR: isolated MFC commit is $actual_mfc_commit; expected $EXPECTED_MFC_COMMIT." >&2
        exit 2
    fi
else
    actual_mfc_commit=directory-name-pinned-0c9a1d43
fi

# MFC Release builds add -march=native.  A binary cached from one Unity CPU
# family can then die with SIGILL on another family.  Pin the isolated build to
# the common AVX2-era x86-64-v3 baseline and keep the Euler build untouched.
PORTABLE_ARCH=x86-64-v3
GPU_CMAKE="$MFC_ILES_ROOT/cmake/GPU.cmake"
[[ -f "$GPU_CMAKE" ]] || { echo "ERROR: missing $GPU_CMAKE" >&2; exit 2; }
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
            f"ERROR: unexpected {path} state for {native}: "
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
RUN_BASE="$DATA_ROOT/runs/mfc_iles_a40/${GRID}_t3_${STAMP}"
SMOKE_DIR="$RUN_BASE/smoke"
CASE_DIR="$RUN_BASE/$GRID"
mkdir -p "$SMOKE_DIR" "$CASE_DIR"

for destination in "$SMOKE_DIR" "$CASE_DIR"; do
    curl -fL --retry 3 "$RAW_BASE/case_iles.py" -o "$destination/case.py"
    curl -fL --retry 3 "$RAW_BASE/Diamond_Airfoil_2D_MFC.stl" \
        -o "$destination/Diamond_Airfoil_2D_MFC.stl"
done

[[ "$(grep -c 'facet normal' "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl")" -eq 2 ]] || {
    echo "ERROR: the planar diamond STL must contain exactly two facets." >&2
    exit 2
}

python3 - "$CASE_DIR/case.py" "$GRID" "$FINAL_TIME" "$SAVE_DT" <<'PY'
import json
import math
import subprocess
import sys

path, grid, final_time, save_dt = sys.argv[1:]
raw = subprocess.check_output(
    [sys.executable, path, "--grid", grid, "--final-time", final_time, "--save-dt", save_dt],
    text=True,
)
case = json.loads(raw)
expected_cells = {"f180": (1979, 1799), "f270": (2969, 2699), "f405": (4454, 4049)}
expected_dt = {"f180": 1.0 / 3600.0, "f270": 1.0 / 5400.0, "f405": 1.0 / 8100.0}
for key, value in {
    "m": expected_cells[grid][0],
    "n": expected_cells[grid][1],
    "p": 0,
    "model_eqns": 2,
    "viscous": "T",
    "patch_ib(1)%slip": "F",
    "weno_order": 5,
    "weno_Re_flux": "T",
    "omega_wrt(3)": "T",
    "schlieren_wrt": "T",
}.items():
    if case.get(key) != value:
        raise SystemExit(f"ERROR: {key}={case.get(key)!r}; expected {value!r}")
if not math.isclose(case["dt"], expected_dt[grid], abs_tol=1.0e-15):
    raise SystemExit(f"ERROR: unexpected dt={case['dt']!r}")
if not math.isclose(case["fluid_pp(1)%Re(1)"], 1.0e6 / 3.0, abs_tol=1.0e-9):
    raise SystemExit("ERROR: stored inverse viscosity does not give Re_c=1e6")
expected_snapshots = case["t_step_stop"] // case["t_step_save"] + 1
if expected_snapshots != 61:
    raise SystemExit(f"ERROR: expected 61 snapshots, got {expected_snapshots}")
print(f"PREFLIGHT=PASS GRID={grid} STOP_STEP={case['t_step_stop']} SAVE_EVERY={case['t_step_save']}")
print(f"EXPECTED_SNAPSHOTS={expected_snapshots}")
PY

CASE_SHA256="$(sha256sum "$CASE_DIR/case.py" | awk '{print $1}')"
STL_SHA256="$(sha256sum "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl" | awk '{print $1}')"

SBATCH_FILE="$RUN_BASE/run_mfc_iles.sbatch"
cat >"$SBATCH_FILE" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1

set -Eeuo pipefail
: "${CASE_DIR:?}"
: "${MFC_ILES_ROOT:?}"
: "${GRID:?}"
: "${FINAL_TIME:?}"
: "${SAVE_DT:?}"
: "${BUILD_MODE:?}"
: "${EXPECTED_SNAPSHOTS:?}"
: "${PORTABLE_ARCH:?}"
: "${PORTABLE_CMAKE_SHA256:?}"

GPU_CMAKE="$MFC_ILES_ROOT/cmake/GPU.cmake"
[[ "$(sha256sum "$GPU_CMAKE" | awk '{print $1}')" == "$PORTABLE_CMAKE_SHA256" ]] || {
    echo "ERROR: portable MFC compiler patch changed after submission." >&2
    exit 5
}
grep -q -- "-march=$PORTABLE_ARCH" "$GPU_CMAKE" || {
    echo "ERROR: portable compiler baseline is absent from $GPU_CMAKE." >&2
    exit 5
}
if grep -Eq -- 'COMPILE_LANGUAGE:Fortran>:-march=native|COMPILE_LANGUAGE:Fortran>:-mcpu=native' "$GPU_CMAKE"; then
    echo "ERROR: native CPU tuning must not be used across heterogeneous Unity nodes." >&2
    exit 5
fi

cpu_flags="$(awk -F ': ' '/^flags/{print " " $2 " "; exit}' /proc/cpuinfo)"
for feature in avx avx2 bmi1 bmi2 f16c fma movbe xsave; do
    [[ "$cpu_flags" == *" $feature "* ]] || {
        echo "ERROR: node $HOSTNAME lacks $feature required by $PORTABLE_ARCH." >&2
        exit 5
    }
done
if [[ "$cpu_flags" != *" lzcnt "* && "$cpu_flags" != *" abm "* ]]; then
    echo "ERROR: node $HOSTNAME lacks LZCNT/ABM required by $PORTABLE_ARCH." >&2
    exit 5
fi
{
    echo "portable_arch=$PORTABLE_ARCH"
    echo "hostname=$HOSTNAME"
    lscpu
} >"$CASE_DIR/cpu-${GRID}.txt"

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
MPI_LAUNCHER="$(command -v mpirun)"
[[ -x "$MPI_LAUNCHER" ]] || { echo "ERROR: mpirun not found." >&2; exit 5; }

CASE_ARGS=(--grid "$GRID" --final-time "$FINAL_TIME" --save-dt "$SAVE_DT")
cd "$MFC_ILES_ROOT"
mkdir -p build
LOCK_FILE="$MFC_ILES_ROOT/build/.mfc-iles-a40.lock"
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

grep -aq 'Number of 2D model boundary edges: *4' "$LOG" || {
    echo "ERROR: MFC did not detect four airfoil boundary edges." >&2
    exit 40
}

STOP_STEP="$(python3 "$CASE_DIR/case.py" "${CASE_ARGS[@]}" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["t_step_stop"])')"
SAVE_EVERY="$(python3 "$CASE_DIR/case.py" "${CASE_ARGS[@]}" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["t_step_save"])')"
FINAL_RESTART="$CASE_DIR/restart_data/lustre_${STOP_STEP}.dat"
FINAL_SILO="$CASE_DIR/silo_hdf5/root/collection_${STOP_STEP}.silo"
[[ -s "$FINAL_RESTART" ]] || { echo "ERROR: missing $FINAL_RESTART" >&2; exit 41; }
[[ -s "$FINAL_SILO" ]] || { echo "ERROR: missing $FINAL_SILO" >&2; exit 42; }

restart_count="$(find "$CASE_DIR/restart_data" -maxdepth 1 -type f -name 'lustre_*.dat' | wc -l)"
silo_count="$(find "$CASE_DIR/silo_hdf5/root" -maxdepth 1 -type f -name 'collection_*.silo' | wc -l)"
if (( restart_count != EXPECTED_SNAPSHOTS || silo_count != EXPECTED_SNAPSHOTS )); then
    echo "ERROR: expected $EXPECTED_SNAPSHOTS permanent fields; restart=$restart_count silo=$silo_count" >&2
    exit 43
fi

INVENTORY="$CASE_DIR/FIELD_INVENTORY.tsv"
printf 'step\ttime\trestart_bytes\tsilo_bytes\n' >"$INVENTORY"
for (( step=0; step<=STOP_STEP; step+=SAVE_EVERY )); do
    restart="$CASE_DIR/restart_data/lustre_${step}.dat"
    silo="$CASE_DIR/silo_hdf5/root/collection_${step}.silo"
    [[ -s "$restart" && -s "$silo" ]] || { echo "ERROR: incomplete field at step $step" >&2; exit 44; }
    time_value="$(python3 -c "print(${step} * ${FINAL_TIME} / ${STOP_STEP})")"
    printf '%s\t%s\t%s\t%s\n' "$step" "$time_value" \
        "$(stat -c %s "$restart")" "$(stat -c %s "$silo")" >>"$INVENTORY"
done

printf 'status=PASS\ngrid=%s\nfinal_time=%s\nsnapshots=%s\nfinal_restart=%s\nfinal_silo=%s\n' \
    "$GRID" "$FINAL_TIME" "$EXPECTED_SNAPSHOTS" "$FINAL_RESTART" "$FINAL_SILO" | \
    tee "$CASE_DIR/RUN_OK_MFC_ILES.txt"
SBATCH

ENV_FILE="$RUN_BASE/submission.env"
{
    printf 'RUN_BASE=%q\n' "$RUN_BASE"
    printf 'CASE_DIR=%q\n' "$CASE_DIR"
    printf 'SMOKE_DIR=%q\n' "$SMOKE_DIR"
    printf 'MFC_ILES_ROOT=%q\n' "$MFC_ILES_ROOT"
    printf 'GRID=%q\n' "$GRID"
    printf 'FINAL_TIME=%q\n' "$FINAL_TIME"
    printf 'SAVE_DT=%q\n' "$SAVE_DT"
    printf 'CASE_SHA256=%q\n' "$CASE_SHA256"
    printf 'STL_SHA256=%q\n' "$STL_SHA256"
    printf 'MFC_COMMIT=%q\n' "$actual_mfc_commit"
    printf 'PORTABLE_ARCH=%q\n' "$PORTABLE_ARCH"
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
        --job-name=mfc-iles-smoke \
        --output="$SMOKE_DIR/slurm-%j.out" --error="$SMOKE_DIR/slurm-%j.err" \
        --export="ALL,CASE_DIR=$SMOKE_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,GRID=smoke,FINAL_TIME=0.05,SAVE_DT=0.025,BUILD_MODE=scratch,EXPECTED_SNAPSHOTS=3,PORTABLE_ARCH=$PORTABLE_ARCH,PORTABLE_CMAKE_SHA256=$PORTABLE_CMAKE_SHA256" \
        "$SBATCH_FILE"
})"
SMOKE_JOB="${SMOKE_JOB%%;*}"
[[ "$SMOKE_JOB" =~ ^[0-9]+$ ]] || { echo "ERROR: invalid smoke job ID '$SMOKE_JOB'" >&2; exit 4; }

PROD_JOB="$({
    cd "$CASE_DIR"
    sbatch --parsable --ntasks="$PROD_NTASKS" --mem="$PROD_MEMORY" --time="$PROD_WALLTIME" \
        --constraint='intel&x86_64_v4' --dependency="afterok:$SMOKE_JOB" \
        --job-name="mfc-iles-a40-$GRID" \
        --output="$CASE_DIR/slurm-%j.out" --error="$CASE_DIR/slurm-%j.err" \
        --export="ALL,CASE_DIR=$CASE_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,GRID=$GRID,FINAL_TIME=$FINAL_TIME,SAVE_DT=$SAVE_DT,BUILD_MODE=nobuild,EXPECTED_SNAPSHOTS=61,PORTABLE_ARCH=$PORTABLE_ARCH,PORTABLE_CMAKE_SHA256=$PORTABLE_CMAKE_SHA256" \
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
echo "COMPILE_BASELINE=$PORTABLE_ARCH (portable across constrained Unity nodes)"
echo "FIELDS=61 permanent restart + Silo snapshots; no pruning"
echo "STATUS: source '$ENV_FILE'; squeue -j \"\$JOBS\""
