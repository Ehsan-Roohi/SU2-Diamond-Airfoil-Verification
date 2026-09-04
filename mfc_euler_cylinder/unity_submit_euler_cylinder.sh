#!/usr/bin/env bash
set -Eeuo pipefail

# One-command Unity launcher for continuum circular-cylinder cases.  The
# default remains the inviscid Euler/slip bow-shock case.  A positive
# REYNOLDS enables the viscous/no-slip shock--wake mode.

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
REYNOLDS="${REYNOLDS:-0}"
CFL_COEFFICIENT="${CFL_COEFFICIENT:-auto}"
AFTER_JOB="${AFTER_JOB:-none}"
RECOVER_CASE_DIR="${RECOVER_CASE_DIR:-}"
RECOVERY_DATA_ROOT="${RECOVERY_DATA_ROOT:-/scratch4/workspace/roohie_umass_edu-mfc-a40-cv/mfc_euler_cylinder_recovery}"
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
if [[ ! "$REYNOLDS" =~ ^[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?$ ]]; then
    echo "ERROR: REYNOLDS must be zero for Euler or a positive number." >&2
    exit 2
fi
python3 - "$REYNOLDS" <<'PY'
import sys
reynolds = float(sys.argv[1])
if reynolds < 0 or 0 < reynolds < 100:
    raise SystemExit("ERROR: REYNOLDS must be zero or at least 100")
PY
if [[ "$CFL_COEFFICIENT" != auto && ! "$CFL_COEFFICIENT" =~ ^[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?$ ]]; then
    echo "ERROR: CFL_COEFFICIENT must be 'auto' or a positive number." >&2
    exit 2
fi
CFL_TAG="$(python3 - "$CFL_COEFFICIENT" <<'PY'
import math
import sys

text = sys.argv[1]
if text == "auto":
    print("default")
else:
    value = float(text)
    if not math.isfinite(value) or not 0.0 < value <= 0.5:
        raise SystemExit("ERROR: CFL_COEFFICIENT must be in (0, 0.5]")
    print(f"{value:.8g}".replace(".", "p").replace("+", ""))
PY
)"
if [[ "$CFL_TAG" == default ]]; then
    CFL_RUN_SUFFIX=""
    CFL_JOB_SUFFIX=""
else
    CFL_RUN_SUFFIX="_cfl${CFL_TAG}"
    CFL_JOB_SUFFIX="-cfl${CFL_TAG}"
fi
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

# A production simulation may finish all checkpoints while the multi-rank
# Silo post-processor crashes during library teardown.  Recovery never edits
# that source run: it links the immutable restart files into a new directory,
# retries Silo with one MPI rank, and falls back to MFC binary output if the
# serial Silo process still exits nonzero.
if [[ -n "$RECOVER_CASE_DIR" ]]; then
    SOURCE_CASE_DIR="${RECOVER_CASE_DIR%/}"
    if [[ "$SOURCE_CASE_DIR" != /* || "$SOURCE_CASE_DIR" == / ]]; then
        echo "ERROR: RECOVER_CASE_DIR must be a non-root absolute path." >&2
        exit 2
    fi
    SOURCE_RESTART="$SOURCE_CASE_DIR/restart_data"
    [[ -d "$SOURCE_RESTART" ]] || {
        echo "ERROR: missing source restart directory: $SOURCE_RESTART" >&2
        exit 2
    }
    if [[ "$RECOVERY_DATA_ROOT" != /* || "$RECOVERY_DATA_ROOT" == / ]]; then
        echo "ERROR: RECOVERY_DATA_ROOT must be a non-root absolute path." >&2
        exit 2
    fi

    STAMP="$(date +%Y%m%d-%H%M%S)"
    SOURCE_RUN_TAG="$(basename "$(dirname "$SOURCE_CASE_DIR")")"
    RECOVERY_DIR="$RECOVERY_DATA_ROOT/${SOURCE_RUN_TAG}_${GRID}_post_${STAMP}"
    mkdir -p "$RECOVERY_DIR/restart_data"
    for name in case.py rankine_hugoniot_reference.py validation_protocol.json vortex_sensitivity_protocol.json; do
        curl -fL --retry 3 "$RAW_BASE/$name" -o "$RECOVERY_DIR/$name"
    done

    RECOVERY_CASE_ARGS=(--mach "$MACH" --grid "$GRID" \
        --final-time "$FINAL_TIME" --save-dt "$SAVE_DT")
    if [[ "$CFL_COEFFICIENT" != auto ]]; then
        RECOVERY_CASE_ARGS+=(--cfl-coefficient "$CFL_COEFFICIENT")
    fi
    if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) > 0 else 1)" "$REYNOLDS"; then
        RECOVERY_CASE_ARGS+=(--reynolds "$REYNOLDS")
    fi
    read -r STOP_STEP SAVE_EVERY EXPECTED_SNAPSHOTS < <(
        python3 "$RECOVERY_DIR/case.py" "${RECOVERY_CASE_ARGS[@]}" --format binary | \
            python3 -c 'import json,sys; c=json.load(sys.stdin); print(c["t_step_stop"],c["t_step_save"],c["t_step_stop"]//c["t_step_save"]+1)'
    )
    (( EXPECTED_SNAPSHOTS >= 3 && EXPECTED_SNAPSHOTS <= 501 )) || {
        echo "ERROR: unreasonable recovery checkpoint count: $EXPECTED_SNAPSHOTS." >&2
        exit 3
    }

    reference_size=""
    for (( step=0; step<=STOP_STEP; step+=SAVE_EVERY )); do
        source="$SOURCE_RESTART/lustre_${step}.dat"
        target="$RECOVERY_DIR/restart_data/lustre_${step}.dat"
        [[ -s "$source" ]] || {
            echo "ERROR: missing or empty source checkpoint: $source" >&2
            exit 3
        }
        size="$(stat -c %s "$source")"
        if [[ -z "$reference_size" ]]; then
            reference_size="$size"
        elif [[ "$size" != "$reference_size" ]]; then
            echo "ERROR: checkpoint $source has $size bytes; expected $reference_size." >&2
            exit 3
        fi
        ln -s "$source" "$target"
    done
    for name in lustre_x_cb.dat lustre_y_cb.dat lustre_ib.dat; do
        source="$SOURCE_RESTART/$name"
        [[ -s "$source" ]] || { echo "ERROR: missing $source" >&2; exit 3; }
        target="$RECOVERY_DIR/restart_data/$name"
        ln -s "$source" "$target"
    done
    while IFS= read -r -d '' source; do
        target="$RECOVERY_DIR/restart_data/$(basename "$source")"
        [[ -e "$target" || -L "$target" ]] || ln -s "$source" "$target"
    done < <(find "$SOURCE_RESTART" -maxdepth 1 -type f -name 'ib_state_*.dat' -print0)

    python3 "$RECOVERY_DIR/rankine_hugoniot_reference.py" --mach "$MACH" \
        --output "$RECOVERY_DIR/RH_REFERENCE.json"
    RECOVERY_SBATCH="$RECOVERY_DIR/run_post_recovery.sbatch"
    cat >"$RECOVERY_SBATCH" <<'RECOVERY_SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --constraint=intel&x86_64_v4

set -Eeuo pipefail
: "${RECOVERY_DIR:?}"
: "${SOURCE_CASE_DIR:?}"
: "${MFC_CYL_ROOT:?}"
: "${MACH:?}"
: "${GRID:?}"
: "${FINAL_TIME:?}"
: "${SAVE_DT:?}"
: "${REYNOLDS:?}"
: "${CFL_COEFFICIENT:?}"
: "${EXPECTED_SNAPSHOTS:?}"
: "${PORTABLE_CMAKE_SHA256:?}"

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
GPU_CMAKE="$MFC_CYL_ROOT/cmake/GPU.cmake"
[[ "$(sha256sum "$GPU_CMAKE" | awk '{print $1}')" == "$PORTABLE_CMAKE_SHA256" ]] || {
    echo "ERROR: MFC compiler configuration changed after submission." >&2
    exit 5
}

CASE_ARGS=(--mach "$MACH" --grid "$GRID" --final-time "$FINAL_TIME" \
    --save-dt "$SAVE_DT")
if [[ "$CFL_COEFFICIENT" != auto ]]; then
    CASE_ARGS+=(--cfl-coefficient "$CFL_COEFFICIENT")
fi
if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) > 0 else 1)" "$REYNOLDS"; then
    CASE_ARGS+=(--reynolds "$REYNOLDS")
fi
cd "$MFC_CYL_ROOT"
LOCK_FILE="$MFC_CYL_ROOT/build/.mfc-euler-cylinder.lock"
if command -v flock >/dev/null 2>&1 && [[ "${MFC_LOCK_HELD:-0}" != 1 ]]; then
    export MFC_LOCK_HELD=1
    exec flock -s -w 7200 "$LOCK_FILE" bash "$0"
fi

./mfc.sh validate "$RECOVERY_DIR/case.py" -- "${CASE_ARGS[@]}" --format silo \
    2>&1 | tee "$RECOVERY_DIR/validate-post-recovery.log"

read -r STOP_STEP SAVE_EVERY < <(
    python3 "$RECOVERY_DIR/case.py" "${CASE_ARGS[@]}" --format binary | \
        python3 -c 'import json,sys; c=json.load(sys.stdin); print(c["t_step_stop"],c["t_step_save"])'
)

set +e
./mfc.sh run "$RECOVERY_DIR/case.py" -n 1 -j 1 --mpi --no-gpu \
    --binary mpirun --no-build -t post_process -- "${CASE_ARGS[@]}" --format silo \
    2>&1 | tee "$RECOVERY_DIR/post-serial-silo.log"
silo_status=${PIPESTATUS[0]}
set -e

silo_complete=1
for (( step=0; step<=STOP_STEP; step+=SAVE_EVERY )); do
    [[ -s "$RECOVERY_DIR/silo_hdf5/root/collection_${step}.silo" ]] || silo_complete=0
done
leaf_count=0
if [[ -d "$RECOVERY_DIR/silo_hdf5" ]]; then
    leaf_count="$(find "$RECOVERY_DIR/silo_hdf5" -mindepth 2 -type f -name '*.silo' ! -path '*/root/*' | wc -l)"
fi
(( leaf_count >= EXPECTED_SNAPSHOTS )) || silo_complete=0

output_mode=silo
if (( silo_status != 0 || silo_complete != 1 )); then
    if [[ -d "$RECOVERY_DIR/silo_hdf5" ]]; then
        mv "$RECOVERY_DIR/silo_hdf5" "$RECOVERY_DIR/silo_hdf5_failed_serial"
    fi
    set +e
    ./mfc.sh run "$RECOVERY_DIR/case.py" -n 1 -j 1 --mpi --no-gpu \
        --binary mpirun --no-build -t post_process -- "${CASE_ARGS[@]}" --format binary \
        2>&1 | tee "$RECOVERY_DIR/post-serial-binary.log"
    binary_status=${PIPESTATUS[0]}
    set -e
    (( binary_status == 0 )) || {
        echo "ERROR: both serial Silo and serial binary post-processing failed." >&2
        exit "$binary_status"
    }
    output_mode=binary
fi

INVENTORY="$RECOVERY_DIR/POSTPROCESS_INVENTORY.tsv"
printf 'step\ttime\trestart_bytes\tproduct\tproduct_bytes\n' >"$INVENTORY"
DT="$(python3 "$RECOVERY_DIR/case.py" "${CASE_ARGS[@]}" --format binary | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["dt"])')"
for (( step=0; step<=STOP_STEP; step+=SAVE_EVERY )); do
    restart="$RECOVERY_DIR/restart_data/lustre_${step}.dat"
    if [[ "$output_mode" == silo ]]; then
        product="$RECOVERY_DIR/silo_hdf5/root/collection_${step}.silo"
    else
        product="$(find "$RECOVERY_DIR/binary" -type f -name "${step}.dat" -print -quit)"
    fi
    [[ -s "$product" ]] || { echo "ERROR: missing recovered product for step $step" >&2; exit 44; }
    time_value="$(python3 -c "print(${step} * ${DT})")"
    printf '%s\t%s\t%s\t%s\t%s\n' "$step" "$time_value" \
        "$(stat -c %s "$restart")" "$product" "$(stat -c %s "$product")" >>"$INVENTORY"
done

final_product="$(tail -n 1 "$INVENTORY" | cut -f4)"
RUN_OK_PATH="$RECOVERY_DIR/RUN_OK_MFC_CYLINDER_RECOVERED.txt"
printf 'status=PASS\nsource_case=%s\nreynolds=%s\ncfl_coefficient=%s\nrecovery_dir=%s\noutput_mode=%s\nsnapshots=%s\nfinal_product=%s\n' \
    "$SOURCE_CASE_DIR" "$REYNOLDS" "$CFL_COEFFICIENT" "$RECOVERY_DIR" "$output_mode" "$EXPECTED_SNAPSHOTS" \
    "$final_product" | tee "$RUN_OK_PATH"
if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) == 0 else 1)" "$REYNOLDS"; then
    cp "$RUN_OK_PATH" "$RECOVERY_DIR/RUN_OK_MFC_EULER_CYLINDER_RECOVERED.txt"
else
    cp "$RUN_OK_PATH" "$RECOVERY_DIR/RUN_OK_MFC_VISCOUS_CYLINDER_RECOVERED.txt"
fi
RECOVERY_SBATCH

    RECOVERY_JOB="$(sbatch --parsable \
        --job-name=mfc-euler-cyl-post-recover \
        --output="$RECOVERY_DIR/slurm-%j.out" \
        --error="$RECOVERY_DIR/slurm-%j.err" \
        --export="ALL,RECOVERY_DIR=$RECOVERY_DIR,SOURCE_CASE_DIR=$SOURCE_CASE_DIR,MFC_CYL_ROOT=$MFC_CYL_ROOT,MACH=$MACH,GRID=$GRID,FINAL_TIME=$FINAL_TIME,SAVE_DT=$SAVE_DT,REYNOLDS=$REYNOLDS,CFL_COEFFICIENT=$CFL_COEFFICIENT,EXPECTED_SNAPSHOTS=$EXPECTED_SNAPSHOTS,PORTABLE_CMAKE_SHA256=$PORTABLE_CMAKE_SHA256" \
        "$RECOVERY_SBATCH")"
    RECOVERY_JOB="${RECOVERY_JOB%%;*}"
    [[ "$RECOVERY_JOB" =~ ^[0-9]+$ ]] || {
        echo "ERROR: invalid recovery job ID '$RECOVERY_JOB'" >&2
        exit 4
    }
    RECOVERY_ENV="$RECOVERY_DIR/submission.env"
    {
        printf 'SOURCE_CASE_DIR=%q\n' "$SOURCE_CASE_DIR"
        printf 'RECOVERY_DIR=%q\n' "$RECOVERY_DIR"
        printf 'RECOVERY_JOB=%q\n' "$RECOVERY_JOB"
        printf 'CFL_COEFFICIENT=%q\n' "$CFL_COEFFICIENT"
    } >"$RECOVERY_ENV"
    echo "SOURCE_CASE_DIR=$SOURCE_CASE_DIR"
    echo "RECOVERY_DIR=$RECOVERY_DIR"
    echo "RECOVERY_JOB=$RECOVERY_JOB"
    echo "RECOVERY_ENV=$RECOVERY_ENV"
    echo "NEXT: squeue -j $RECOVERY_JOB"
    exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
MACH_TAG="${MACH/./p}"
read -r CASE_FAMILY PHYSICS_TAG < <(
    python3 - "$REYNOLDS" <<'PY'
import sys
reynolds = float(sys.argv[1])
if reynolds == 0:
    print("mfc_euler_cylinder euler")
else:
    tag = f"{reynolds:.8g}".replace("+", "").replace(".", "p")
    print(f"mfc_viscous_cylinder re{tag}")
PY
)
RUN_BASE="$DATA_ROOT/runs/$CASE_FAMILY/m${MACH_TAG}_${PHYSICS_TAG}_${GRID}${CFL_RUN_SUFFIX}_${STAMP}"
SMOKE_DIR="$RUN_BASE/smoke"
CASE_DIR="$RUN_BASE/$GRID"
mkdir -p "$SMOKE_DIR" "$CASE_DIR"

for destination in "$SMOKE_DIR" "$CASE_DIR"; do
    curl -fL --retry 3 "$RAW_BASE/case.py" -o "$destination/case.py"
    curl -fL --retry 3 "$RAW_BASE/rankine_hugoniot_reference.py" \
        -o "$destination/rankine_hugoniot_reference.py"
    for name in validation_protocol.json vortex_sensitivity_protocol.json; do
        curl -fL --retry 3 "$RAW_BASE/$name" -o "$destination/$name"
    done
done

EXPECTED_SNAPSHOTS="$(python3 - "$CASE_DIR/case.py" "$MACH" "$GRID" "$FINAL_TIME" "$SAVE_DT" "$REYNOLDS" "$CFL_COEFFICIENT" <<'PY'
import json
import math
import subprocess
import sys

path, mach, grid, final_time, save_dt, reynolds_text, cfl_text = sys.argv[1:]
reynolds = float(reynolds_text)
command = [sys.executable, path, "--mach", mach, "--grid", grid,
           "--final-time", final_time, "--save-dt", save_dt]
if reynolds > 0:
    command.extend(["--reynolds", reynolds_text])
if cfl_text != "auto":
    command.extend(["--cfl-coefficient", cfl_text])
raw = subprocess.check_output(command, text=True)
case = json.loads(raw)
expected_indices = {
    "f90": (989, 899), "f180": (1979, 1799), "f270": (2969, 2699)
}
for key, value in {
    "m": expected_indices[grid][0],
    "n": expected_indices[grid][1],
    "p": 0,
    "model_eqns": 2,
    "patch_ib(1)%geometry": 2,
    "patch_ib(1)%radius": 0.5,
    "riemann_solver": 2,
    "weno_order": 5,
    "bc_x%beg": -11,
    "bc_x%end": -12,
}.items():
    if case.get(key) != value:
        raise SystemExit(f"ERROR: {key}={case.get(key)!r}; expected {value!r}")
if reynolds == 0:
    if case.get("viscous") != "F" or case.get("patch_ib(1)%slip") != "T":
        raise SystemExit("ERROR: Euler mode must be inviscid and slip")
    if any(key.startswith("fluid_pp(1)%Re") for key in case):
        raise SystemExit("ERROR: Euler case must not define viscosity/Re parameters")
else:
    if case.get("viscous") != "T" or case.get("patch_ib(1)%slip") != "F":
        raise SystemExit("ERROR: viscous mode must be viscous and no-slip")
    expected_inverse_mu = reynolds / float(mach)
    if not math.isclose(case.get("fluid_pp(1)%Re(1)", -1.0), expected_inverse_mu,
                        rel_tol=1e-12, abs_tol=1e-12):
        raise SystemExit("ERROR: viscous Reynolds parameter is inconsistent")
if not math.isclose(case["patch_icpp(1)%vel(1)"], float(mach), abs_tol=1e-14):
    raise SystemExit("ERROR: nondimensional freestream velocity must equal Mach")
expected_snapshots = case["t_step_stop"] // case["t_step_save"] + 1
if not 3 <= expected_snapshots <= 501:
    raise SystemExit(f"ERROR: unreasonable production snapshot count {expected_snapshots}")
print(expected_snapshots)
PY
)"
echo "PREFLIGHT=PASS MACH=$MACH REYNOLDS=$REYNOLDS GRID=$GRID CFL=$CFL_COEFFICIENT SNAPSHOTS=$EXPECTED_SNAPSHOTS"

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
: "${REYNOLDS:?}"
: "${CFL_COEFFICIENT:?}"
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
if [[ "$CFL_COEFFICIENT" != auto ]]; then
    CASE_ARGS+=(--cfl-coefficient "$CFL_COEFFICIENT")
fi
if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) > 0 else 1)" "$REYNOLDS"; then
    CASE_ARGS+=(--reynolds "$REYNOLDS")
fi
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
    "${build_args[@]}" -t pre_process simulation -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$LOG"
mfc_status=${PIPESTATUS[0]}
set -e
(( mfc_status == 0 )) || exit "$mfc_status"

STOP_STEP="$(python3 "$CASE_DIR/case.py" "${CASE_ARGS[@]}" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["t_step_stop"])')"
SAVE_EVERY="$(python3 "$CASE_DIR/case.py" "${CASE_ARGS[@]}" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["t_step_save"])')"
FINAL_RESTART="$CASE_DIR/restart_data/lustre_${STOP_STEP}.dat"
[[ -s "$FINAL_RESTART" ]] || { echo "ERROR: missing $FINAL_RESTART" >&2; exit 41; }

restart_count="$(find "$CASE_DIR/restart_data" -maxdepth 1 -type f -name 'lustre_[0-9]*.dat' | wc -l)"
if (( restart_count < EXPECTED_SNAPSHOTS )); then
    echo "ERROR: expected $EXPECTED_SNAPSHOTS restart states; found $restart_count" >&2
    exit 43
fi

post_build_args=(--no-build)
if [[ "$BUILD_MODE" == scratch ]]; then
    # The first smoke call compiled only pre_process and simulation.  Permit
    # one incremental build of post_process; production then reuses it.
    post_build_args=()
fi
set +e
./mfc.sh run "$CASE_DIR/case.py" -n 1 -j 1 --mpi --no-gpu --binary mpirun \
    "${post_build_args[@]}" -t post_process -- "${CASE_ARGS[@]}" --format silo \
    2>&1 | tee "$CASE_DIR/post-serial-silo.log"
silo_status=${PIPESTATUS[0]}
set -e

silo_complete=1
for (( step=0; step<=STOP_STEP; step+=SAVE_EVERY )); do
    [[ -s "$CASE_DIR/silo_hdf5/root/collection_${step}.silo" ]] || silo_complete=0
done
leaf_count=0
if [[ -d "$CASE_DIR/silo_hdf5" ]]; then
    leaf_count="$(find "$CASE_DIR/silo_hdf5" -mindepth 2 -type f -name '*.silo' ! -path '*/root/*' | wc -l)"
fi
(( leaf_count >= EXPECTED_SNAPSHOTS )) || silo_complete=0

output_mode=silo
if (( silo_status != 0 || silo_complete != 1 )); then
    if [[ -d "$CASE_DIR/silo_hdf5" ]]; then
        mv "$CASE_DIR/silo_hdf5" "$CASE_DIR/silo_hdf5_failed_serial"
    fi
    set +e
    ./mfc.sh run "$CASE_DIR/case.py" -n 1 -j 1 --mpi --no-gpu --binary mpirun \
        --no-build -t post_process -- "${CASE_ARGS[@]}" --format binary \
        2>&1 | tee "$CASE_DIR/post-serial-binary.log"
    binary_status=${PIPESTATUS[0]}
    set -e
    (( binary_status == 0 )) || {
        echo "ERROR: both serial Silo and serial binary post-processing failed." >&2
        exit "$binary_status"
    }
    output_mode=binary
fi

INVENTORY="$CASE_DIR/FIELD_INVENTORY.tsv"
printf 'step\ttime\trestart_bytes\tproduct\tproduct_bytes\n' >"$INVENTORY"
DT="$(python3 "$CASE_DIR/case.py" "${CASE_ARGS[@]}" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["dt"])')"
for (( step=0; step<=STOP_STEP; step+=SAVE_EVERY )); do
    restart="$CASE_DIR/restart_data/lustre_${step}.dat"
    if [[ "$output_mode" == silo ]]; then
        product="$CASE_DIR/silo_hdf5/root/collection_${step}.silo"
    else
        product="$(find "$CASE_DIR/binary" -type f -name "${step}.dat" -print -quit)"
    fi
    [[ -s "$restart" && -s "$product" ]] || { echo "ERROR: incomplete state $step" >&2; exit 44; }
    time_value="$(python3 -c "print(${step} * ${DT})")"
    printf '%s\t%s\t%s\t%s\t%s\n' "$step" "$time_value" \
        "$(stat -c %s "$restart")" "$product" "$(stat -c %s "$product")" >>"$INVENTORY"
done

FINAL_PRODUCT="$(tail -n 1 "$INVENTORY" | cut -f4)"
RUN_OK_PATH="$CASE_DIR/RUN_OK_MFC_CYLINDER.txt"
printf 'status=PASS\nmach=%s\nreynolds=%s\ngrid=%s\ncfl_coefficient=%s\nfinal_time=%s\nsnapshots=%s\npostprocess_mode=%s\nfinal_restart=%s\nfinal_product=%s\n' \
    "$MACH" "$REYNOLDS" "$GRID" "$CFL_COEFFICIENT" "$(python3 -c "print(${STOP_STEP} * ${DT})")" \
    "$EXPECTED_SNAPSHOTS" "$output_mode" "$FINAL_RESTART" "$FINAL_PRODUCT" | \
    tee "$RUN_OK_PATH"
if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) == 0 else 1)" "$REYNOLDS"; then
    cp "$RUN_OK_PATH" "$CASE_DIR/RUN_OK_MFC_EULER_CYLINDER.txt"
else
    cp "$RUN_OK_PATH" "$CASE_DIR/RUN_OK_MFC_VISCOUS_CYLINDER.txt"
fi
SBATCH

ENV_FILE="$RUN_BASE/submission.env"
{
    printf 'RUN_BASE=%q\n' "$RUN_BASE"
    printf 'CASE_DIR=%q\n' "$CASE_DIR"
    printf 'SMOKE_DIR=%q\n' "$SMOKE_DIR"
    printf 'MFC_CYL_ROOT=%q\n' "$MFC_CYL_ROOT"
    printf 'MACH=%q\n' "$MACH"
    printf 'REYNOLDS=%q\n' "$REYNOLDS"
    printf 'GRID=%q\n' "$GRID"
    printf 'FINAL_TIME=%q\n' "$FINAL_TIME"
    printf 'SAVE_DT=%q\n' "$SAVE_DT"
    printf 'CFL_COEFFICIENT=%q\n' "$CFL_COEFFICIENT"
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
        --export="ALL,CASE_DIR=$SMOKE_DIR,MFC_CYL_ROOT=$MFC_CYL_ROOT,MACH=$MACH,REYNOLDS=$REYNOLDS,CFL_COEFFICIENT=$CFL_COEFFICIENT,GRID=smoke,FINAL_TIME=0.05,SAVE_DT=0.025,BUILD_MODE=scratch,EXPECTED_SNAPSHOTS=3,PORTABLE_CMAKE_SHA256=$PORTABLE_CMAKE_SHA256" \
        "$SBATCH_FILE"
})"
SMOKE_JOB="${SMOKE_JOB%%;*}"
[[ "$SMOKE_JOB" =~ ^[0-9]+$ ]] || { echo "ERROR: invalid smoke job ID '$SMOKE_JOB'" >&2; exit 4; }

PROD_JOB="$({
    cd "$CASE_DIR"
    sbatch --parsable --ntasks="$PROD_NTASKS" --mem="$PROD_MEMORY" --time="$PROD_WALLTIME" \
        --constraint='intel&x86_64_v4' --dependency="afterok:$SMOKE_JOB" \
        --job-name="mfc-cyl-m${MACH_TAG}-${PHYSICS_TAG}-${GRID}${CFL_JOB_SUFFIX}" \
        --output="$CASE_DIR/slurm-%j.out" --error="$CASE_DIR/slurm-%j.err" \
        --export="ALL,CASE_DIR=$CASE_DIR,MFC_CYL_ROOT=$MFC_CYL_ROOT,MACH=$MACH,REYNOLDS=$REYNOLDS,CFL_COEFFICIENT=$CFL_COEFFICIENT,GRID=$GRID,FINAL_TIME=$FINAL_TIME,SAVE_DT=$SAVE_DT,BUILD_MODE=nobuild,EXPECTED_SNAPSHOTS=$EXPECTED_SNAPSHOTS,PORTABLE_CMAKE_SHA256=$PORTABLE_CMAKE_SHA256" \
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
