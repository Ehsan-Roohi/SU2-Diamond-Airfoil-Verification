#!/usr/bin/env bash
set -Eeuo pipefail

# Submit a lightweight post-processing job that converts the completed f405
# Silo result into one small movie-ready ZIP in the repository root.

DEFAULT_ROOT=/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification
ROOT="${ROOT:-$DEFAULT_ROOT}"
PACK_MEMORY="${PACK_MEMORY:-16G}"
PACK_WALLTIME="${PACK_WALLTIME:-02:00:00}"
PACK_STRIDE="${PACK_STRIDE:-6}"

if [[ "$ROOT" != /* || "$ROOT" == / ]]; then
    echo "ERROR: ROOT must be a non-root absolute path." >&2
    exit 2
fi
if [[ ! "$PACK_STRIDE" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: PACK_STRIDE must be a positive integer." >&2
    exit 2
fi

if [[ -z "${RUN_BASE:-}" ]]; then
    while IFS= read -r candidate; do
        if [[ -f "$candidate/f405_t13p5/RUN_OK_F405.txt" ]]; then
            RUN_BASE="$candidate"
            break
        fi
    done < <(
        find "$ROOT/mfc_runs" -maxdepth 1 -type d \
            -name 'fixed_ib_a40_f405_chain_jfm_*' -printf '%T@ %p\n' 2>/dev/null | \
            sort -nr | cut -d' ' -f2-
    )
fi

if [[ -z "${RUN_BASE:-}" || "$RUN_BASE" != "$ROOT"/mfc_runs/* ]]; then
    echo "ERROR: no completed f405 chain was found below $ROOT/mfc_runs." >&2
    exit 2
fi
CASE_DIR="$RUN_BASE/f405_t13p5"
ENV_FILE="$RUN_BASE/submission.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: missing $ENV_FILE" >&2
    exit 2
fi
source "$ENV_FILE"

required_markers=(
    "$CASE_DIR/SEGMENT_1_OK.txt"
    "$CASE_DIR/SEGMENT_2_OK.txt"
    "$CASE_DIR/SEGMENT_3_OK.txt"
    "$CASE_DIR/RUN_OK_F405.txt"
)
for marker in "${required_markers[@]}"; do
    if [[ ! -f "$marker" ]]; then
        echo "ERROR: completion marker is missing: $marker" >&2
        exit 3
    fi
done
if [[ -z "${JOBS:-}" ]]; then
    echo "ERROR: JOBS is missing from $ENV_FILE" >&2
    exit 3
fi
if [[ ! -s "$CASE_DIR/silo_hdf5/p0/0.silo" || \
      ! -s "$CASE_DIR/silo_hdf5/p0/109350.silo" ]]; then
    echo "ERROR: initial or final Silo snapshot is missing." >&2
    exit 3
fi

PACK_PY="$MFC_ROOT/build/venv/bin/python3"
if [[ ! -x "$PACK_PY" ]] || \
   ! "$PACK_PY" -c 'import h5py,numpy' >/dev/null 2>&1; then
    echo "ERROR: MFC Python environment lacks h5py/numpy: $PACK_PY" >&2
    exit 4
fi

PACKER="$RUN_BASE/pack_f405_results.py"
PACKER_RAW=https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/262f889ca0aaaf57295a480db2cfcb1b778c69d6/mfc_grid_convergence/pack_f405_results.py
curl -fL --retry 3 "$PACKER_RAW" -o "$PACKER"
"$PACK_PY" -m py_compile "$PACKER"

SBATCH_FILE="$RUN_BASE/run_pack_f405.sbatch"
cat >"$SBATCH_FILE" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1

set -Eeuo pipefail
: "${ROOT:?}"
: "${RUN_BASE:?}"
: "${CASE_DIR:?}"
: "${MFC_ROOT:?}"
: "${PACKER:?}"
: "${PACK_STRIDE:?}"

source "$RUN_BASE/submission.env"
PACK_PY="$MFC_ROOT/build/venv/bin/python3"
STAGE_DIR="$RUN_BASE/f405_compact_stage_${SLURM_JOB_ID}"
mkdir -p "$STAGE_DIR"
NPZ="$STAGE_DIR/MFC_A40_F405_MOVIE_READY.npz"

"$PACK_PY" "$PACKER" "$CASE_DIR" \
    --output "$NPZ" --stride "$PACK_STRIDE" \
    2>&1 | tee "$STAGE_DIR/pack-f405.log"

{
    echo "created=$(date -Is)"
    echo "source_run=$RUN_BASE"
    echo "case_dir=$CASE_DIR"
    echo "jobs=$JOBS"
    echo "grid=4455x4050"
    echo "dt=1/8100"
    echo "save_every=4374"
    echo "physical_times=0:0.54:13.5"
    echo "crop=x[-1.5,5.5],y[-2.5,4.5]"
    echo "stride=$PACK_STRIDE"
    echo "fields=rho,pres,vel1,vel2,ib_mask,ib_force_x,ib_force_y"
    echo
    echo "===== JOB ACCOUNTING ====="
    sacct -j "$JOBS" -X \
        --format=JobIDRaw,JobName%24,State,ExitCode,Elapsed,Start,End,NodeList%20
    echo
    echo "===== RAW DATA SIZE (NOT IN ZIP) ====="
    du -sh "$CASE_DIR/silo_hdf5" "$CASE_DIR/restart_data" 2>/dev/null || true
    echo
    echo "===== P0 SNAPSHOTS ====="
    find "$CASE_DIR/silo_hdf5/p0" -maxdepth 1 -type f -name '*.silo' \
        -printf '%f\n' | sed 's/\.silo$//' | sort -n | paste -sd, -
} >"$STAGE_DIR/PACKAGE_INFO.txt"

cat >"$STAGE_DIR/README_UPLOAD.txt" <<'EOF'
Upload this ZIP to ChatGPT. The compact NPZ contains all 26 physical f405
snapshots needed for fixed-scale pressure/numerical-Schlieren and
vorticity/streamline movies. Raw restart_data and silo_hdf5 remain on Unity.
The physical snapshot spacing is 0.54; any visual interpolation used to make a
smoother movie adds no new physical data and must not be presented as such.
EOF

shopt -s nullglob
small_files=(
    "$CASE_DIR/case.py"
    "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl"
    "$CASE_DIR/run_time.inf"
    "$CASE_DIR"/SEGMENT_*_OK.txt
    "$CASE_DIR/RUN_OK_F405.txt"
    "$CASE_DIR"/mfc-f405-s*.log
    "$CASE_DIR"/validate-f405-s*.log
    "$CASE_DIR"/slurm-s*.out
    "$CASE_DIR"/slurm-s*.err
    "$RUN_BASE/submission.env"
    "$PACKER"
)
for path in "${small_files[@]}"; do
    [[ -f "$path" ]] || continue
    cp -p "$path" "$STAGE_DIR/"
done

touch "$STAGE_DIR/PACKAGE_OK.txt"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$ROOT/MFC_A40_F405_MOVIE_READY_${STAMP}.zip"

python3 - "$STAGE_DIR" "$ARCHIVE" <<'PY'
from pathlib import Path
import sys
import zipfile

stage = Path(sys.argv[1])
archive = Path(sys.argv[2])
with zipfile.ZipFile(archive, "w", allowZip64=True) as target:
    for path in sorted(stage.iterdir()):
        if not path.is_file():
            continue
        compression = zipfile.ZIP_STORED if path.suffix == ".npz" else zipfile.ZIP_DEFLATED
        target.write(path, arcname=path.name, compress_type=compression)
PY

ARCHIVE_SHA256="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
ARCHIVE_BYTES="$(stat -c '%s' "$ARCHIVE")"
(
    cd "$ROOT"
    sha256sum "$(basename "$ARCHIVE")" >"$(basename "$ARCHIVE").sha256.txt"
)

RESULT_ENV="$ROOT/LAST_MFC_A40_F405_PACKAGE.env"
: >"$RESULT_ENV"
printf 'MFC_F405_ARCHIVE=%q\n' "$ARCHIVE" >>"$RESULT_ENV"
printf 'MFC_F405_ARCHIVE_SHA256=%q\n' "$ARCHIVE_SHA256" >>"$RESULT_ENV"
printf 'MFC_F405_ARCHIVE_BYTES=%q\n' "$ARCHIVE_BYTES" >>"$RESULT_ENV"
printf 'MFC_F405_SOURCE_RUN=%q\n' "$RUN_BASE" >>"$RESULT_ENV"

echo "PACKAGE_OK=$STAGE_DIR/PACKAGE_OK.txt"
echo "ARCHIVE=$ARCHIVE"
echo "ARCHIVE_BYTES=$ARCHIVE_BYTES"
echo "ARCHIVE_SHA256=$ARCHIVE_SHA256"
echo "RESULT_ENV=$RESULT_ENV"
ls -lh "$ARCHIVE" "$ARCHIVE.sha256.txt" "$RESULT_ENV"
SBATCH

PACK_JOB="$(sbatch --parsable \
    --job-name=mfc-pack-f405 \
    --mem="$PACK_MEMORY" --time="$PACK_WALLTIME" \
    --output="$ROOT/mfc-pack-f405-%j.out" \
    --error="$ROOT/mfc-pack-f405-%j.err" \
    --export="ALL,ROOT=$ROOT,RUN_BASE=$RUN_BASE,CASE_DIR=$CASE_DIR,MFC_ROOT=$MFC_ROOT,PACKER=$PACKER,PACK_STRIDE=$PACK_STRIDE" \
    "$SBATCH_FILE")"
PACK_JOB="${PACK_JOB%%;*}"
if [[ ! "$PACK_JOB" =~ ^[0-9]+$ ]]; then
    echo "ERROR: unexpected sbatch response: $PACK_JOB" >&2
    exit 5
fi

JOB_ENV="$ROOT/LAST_MFC_A40_F405_PACK_JOB.env"
: >"$JOB_ENV"
printf 'MFC_F405_PACK_JOB=%q\n' "$PACK_JOB" >>"$JOB_ENV"
printf 'MFC_F405_SOURCE_RUN=%q\n' "$RUN_BASE" >>"$JOB_ENV"
printf 'MFC_F405_PACK_LOG=%q\n' "$ROOT/mfc-pack-f405-${PACK_JOB}.out" >>"$JOB_ENV"

echo "RUN_BASE=$RUN_BASE"
echo "CASE_DIR=$CASE_DIR"
echo "PACK_JOB=$PACK_JOB"
echo "JOB_ENV=$JOB_ENV"
echo "MONITOR: squeue -j $PACK_JOB; tail -f '$ROOT/mfc-pack-f405-${PACK_JOB}.out'"
