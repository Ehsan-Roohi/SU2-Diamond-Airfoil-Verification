#!/usr/bin/env bash
set -euo pipefail

# Run this from the SU2-Diamond-Airfoil-Verification checkout. Environment
# overrides: ROOT, MFC_ROOT, STEPS, SAVE_EVERY, NTASKS, WALLTIME, MEMORY.
ROOT="${ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
STEPS="${STEPS:-4320}"
SAVE_EVERY="${SAVE_EVERY:-108}"
NTASKS="${NTASKS:-32}"
WALLTIME="${WALLTIME:-03:00:00}"
MEMORY="${MEMORY:-120G}"

if [[ -z "${MFC_ROOT:-}" ]]; then
    if [[ -d "$ROOT/third_party/MFC-0c9a1d43" ]]; then
        MFC_ROOT="$ROOT/third_party/MFC-0c9a1d43"
    else
        for candidate in "$ROOT"/third_party/MFC-*; do
            if [[ -d "$candidate" ]]; then
                MFC_ROOT="$candidate"
                break
            fi
        done
    fi
fi
if [[ -z "${MFC_ROOT:-}" || ! -x "$MFC_ROOT/mfc.sh" ]]; then
    echo "ERROR: MFC root not found below $ROOT/third_party" >&2
    echo "Set it explicitly, for example: MFC_ROOT=/path/to/MFC bash ..." >&2
    exit 2
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_BASE="$ROOT/mfc_runs/fixed_ib_a40_startup_${STAMP}"
CASE_DIR="$RUN_BASE/very_fine_startup_t0p8_dt0p02"
mkdir -p "$CASE_DIR"

# Pin companion inputs to a reviewed commit so the submitted case is reproducible.
RAW_BASE="https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/782fa58398e34235b6df5bb375ab02c2e6baa28b/mfc_startup_diagnostics"
curl -fL --retry 3 "$RAW_BASE/case_startup.py" -o "$CASE_DIR/case.py"
curl -fL --retry 3 "$RAW_BASE/pack_startup_fields.py" -o "$CASE_DIR/pack_startup_fields.py"
curl -fL --retry 3 "$RAW_BASE/Diamond_Airfoil_2D_MFC.stl" -o "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl"

python3 "$CASE_DIR/case.py" --steps "$STEPS" --save-every "$SAVE_EVERY" >/dev/null
if [[ "$(grep -c 'facet normal' "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl")" -ne 2 ]]; then
    echo "ERROR: expected the validated two-triangle planar STL" >&2
    exit 2
fi

SBATCH_FILE="$CASE_DIR/run_startup.sbatch"
cat >"$SBATCH_FILE" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1
#SBATCH --job-name=mfc-a40-startup
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err

set -euo pipefail

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1

cd "$MFC_ROOT"
mkdir -p build
exec 9>build/.mfc-a40-startup.lock
if command -v flock >/dev/null 2>&1; then
    flock -w 1800 9
fi

set +e
./mfc.sh run "$CASE_DIR/case.py" \
    -n "$SLURM_NTASKS" -j 1 --mpi --no-gpu \
    2>&1 | tee "$CASE_DIR/mfc-startup.log"
mfc_status=${PIPESTATUS[0]}
set -e
if [[ "$mfc_status" -ne 0 ]]; then
    echo "MFC failed with exit code $mfc_status" >&2
    exit "$mfc_status"
fi

grep -am1 'Number of 2D model boundary edges' "$CASE_DIR/mfc-startup.log"
grep -aq 'Number of 2D model boundary edges: *4' "$CASE_DIR/mfc-startup.log" || {
    echo "ERROR: MFC did not detect four airfoil boundary edges" >&2
    exit 3
}
touch "$CASE_DIR/RUN_OK.txt"

PACK_PY="$MFC_ROOT/build/venv/bin/python3"
if [[ ! -x "$PACK_PY" ]] || ! "$PACK_PY" -c 'import h5py,numpy' >/dev/null 2>&1; then
    echo "ERROR: MFC Python environment lacks h5py/numpy; raw result is still valid." >&2
    exit 4
fi

"$PACK_PY" "$CASE_DIR/pack_startup_fields.py" "$CASE_DIR" \
    --output "$CASE_DIR/MFC_A40_STARTUP_COMPACT.npz" \
    2>&1 | tee "$CASE_DIR/pack-startup.log"

cat >"$CASE_DIR/UPLOAD_INSTRUCTIONS.txt" <<'EOF'
Upload every MFC_A40_STARTUP_*.part file plus PARTS.sha256 and ORIGINAL.sha256.
Do not upload restart_data/ or silo_hdf5/: the compact NPZ contains the fields
needed to reconstruct pressure/schlieren/vorticity/streamline movies and loads.
EOF

BUNDLE="$CASE_DIR/MFC_A40_STARTUP_FOR_CHATGPT.tar"
tar -C "$CASE_DIR" -cf "$BUNDLE" \
    MFC_A40_STARTUP_COMPACT.npz case.py Diamond_Airfoil_2D_MFC.stl \
    RUN_OK.txt mfc-startup.log pack-startup.log run_time.inf UPLOAD_INSTRUCTIONS.txt

UPLOAD_DIR="$CASE_DIR/upload_parts"
mkdir -p "$UPLOAD_DIR"
(
    cd "$CASE_DIR"
    sha256sum "$(basename "$BUNDLE")" >"$UPLOAD_DIR/ORIGINAL.sha256"
)
split -b 90M -d -a 3 "$BUNDLE" "$UPLOAD_DIR/MFC_A40_STARTUP_"
for path in "$UPLOAD_DIR"/MFC_A40_STARTUP_*; do
    mv "$path" "$path.part"
done
(
    cd "$UPLOAD_DIR"
    sha256sum MFC_A40_STARTUP_*.part >PARTS.sha256
)

echo "RUN_OK=$CASE_DIR/RUN_OK.txt"
echo "UPLOAD_DIR=$UPLOAD_DIR"
du -sh "$CASE_DIR/restart_data" "$CASE_DIR/silo_hdf5" "$UPLOAD_DIR" 2>/dev/null || true
SBATCH

cat >"$RUN_BASE/submission.env" <<EOF
RUN_BASE=$RUN_BASE
CASE_DIR=$CASE_DIR
MFC_ROOT=$MFC_ROOT
STEPS=$STEPS
SAVE_EVERY=$SAVE_EVERY
EOF

(
    cd "$CASE_DIR"
    JOB_ID="$(sbatch --parsable \
        --ntasks="$NTASKS" --mem="$MEMORY" --time="$WALLTIME" \
        --export=ALL,CASE_DIR="$CASE_DIR",MFC_ROOT="$MFC_ROOT" \
        "$SBATCH_FILE")"
    echo "JOB_ID=$JOB_ID" | tee -a "$RUN_BASE/submission.env"
)

echo "RUN_BASE=$RUN_BASE"
echo "CASE_DIR=$CASE_DIR"
echo "MFC_ROOT=$MFC_ROOT"
echo "STEPS=$STEPS"
echo "SAVE_EVERY=$SAVE_EVERY"
grep '^JOB_ID=' "$RUN_BASE/submission.env"
echo "STATUS: source '$RUN_BASE/submission.env'; squeue -j \"\$JOB_ID\""
