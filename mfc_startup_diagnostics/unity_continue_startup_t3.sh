#!/usr/bin/env bash
set -euo pipefail

# Continue the completed high-cadence t=0..0.8 run to t=3.0, in place, from
# its saved restart at step 4320. The original 41 Silo frames remain intact;
# the continuation appends 110 new frames at Delta(t)=0.02.

DEFAULT_ROOT=/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification
if [[ -z "${ROOT:-}" ]]; then
    git_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -n "$git_root" && -d "$git_root/mfc_startup_diagnostics" ]]; then
        ROOT="$git_root"
    elif [[ -d "$DEFAULT_ROOT" ]]; then
        ROOT="$DEFAULT_ROOT"
    else
        ROOT="$(pwd)"
    fi
fi

START_STEP="${START_STEP:-4320}"
STOP_STEP="${STOP_STEP:-16200}"
SAVE_EVERY="${SAVE_EVERY:-108}"
NTASKS="${NTASKS:-32}"
WALLTIME="${WALLTIME:-05:00:00}"
MEMORY="${MEMORY:-120G}"

if [[ "$START_STEP" -ne 4320 ]]; then
    echo "ERROR: this continuation is validated for the completed step-4320 startup run." >&2
    exit 2
fi
if (( STOP_STEP <= START_STEP || START_STEP % SAVE_EVERY || STOP_STEP % SAVE_EVERY )); then
    echo "ERROR: require STOP_STEP>START_STEP and both divisible by SAVE_EVERY." >&2
    exit 2
fi

if [[ -z "${MFC_ROOT:-}" ]]; then
    if [[ -x "$ROOT/third_party/MFC-0c9a1d43/mfc.sh" ]]; then
        MFC_ROOT="$ROOT/third_party/MFC-0c9a1d43"
    else
        for candidate in "$ROOT"/third_party/MFC-*; do
            if [[ -x "$candidate/mfc.sh" ]]; then
                MFC_ROOT="$candidate"
                break
            fi
        done
    fi
fi
if [[ -z "${MFC_ROOT:-}" || ! -x "$MFC_ROOT/mfc.sh" ]]; then
    echo "ERROR: MFC root not found. Set MFC_ROOT=/path/to/MFC explicitly." >&2
    exit 2
fi

# SOURCE_RUN_BASE may be supplied explicitly. Otherwise select the newest
# successful startup directory that has both RUN_OK and the required restart.
if [[ -z "${SOURCE_RUN_BASE:-}" ]]; then
    shopt -s nullglob
    candidates=("$ROOT"/mfc_runs/fixed_ib_a40_startup_*)
    shopt -u nullglob
    for ((idx=${#candidates[@]}-1; idx>=0; idx--)); do
        candidate="${candidates[$idx]}"
        candidate_case="$candidate/very_fine_startup_t0p8_dt0p02"
        if [[ -f "$candidate_case/RUN_OK.txt" && \
              -s "$candidate_case/restart_data/lustre_${START_STEP}.dat" ]]; then
            SOURCE_RUN_BASE="$candidate"
            break
        fi
    done
fi
if [[ -z "${SOURCE_RUN_BASE:-}" ]]; then
    echo "ERROR: no completed startup run with restart step $START_STEP was found." >&2
    echo "Set SOURCE_RUN_BASE=/.../fixed_ib_a40_startup_YYYYMMDD-HHMMSS" >&2
    exit 2
fi

CASE_DIR="$SOURCE_RUN_BASE/very_fine_startup_t0p8_dt0p02"
RESTART_FILE="$CASE_DIR/restart_data/lustre_${START_STEP}.dat"
if [[ ! -f "$CASE_DIR/RUN_OK.txt" || ! -s "$RESTART_FILE" ]]; then
    echo "ERROR: source run is incomplete or $RESTART_FILE is missing." >&2
    exit 2
fi
if [[ ! -f "$CASE_DIR/pack_startup_fields.py" || \
      ! -f "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl" ]]; then
    echo "ERROR: source case is missing its packer or validated STL." >&2
    exit 2
fi

# This immutable ref is filled by the publishing commit.
RAW_BASE="https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/7e857fb886169a126e2cadecb4f31349f4785858/mfc_startup_diagnostics"
curl -fL --retry 3 "$RAW_BASE/case_continue_t3.py" -o "$CASE_DIR/case_continue_t3.py"
python3 "$CASE_DIR/case_continue_t3.py" \
    --start-step "$START_STEP" --stop-step "$STOP_STEP" \
    --save-every "$SAVE_EVERY" >/dev/null

if [[ "$(grep -c 'facet normal' "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl")" -ne 2 ]]; then
    echo "ERROR: expected the validated two-triangle planar STL." >&2
    exit 2
fi

echo "SOURCE_RUN_BASE=$SOURCE_RUN_BASE"
echo "CASE_DIR=$CASE_DIR"
echo "RESTART_FILE=$RESTART_FILE"
echo "CONTINUATION=t=$((START_STEP / 5400)) to t=$(awk -v s="$STOP_STEP" 'BEGIN{printf "%.3f",s/5400}')"
echo "NEW_FRAMES=$(((STOP_STEP - START_STEP) / SAVE_EVERY))"
df -h "$CASE_DIR" || true

SBATCH_FILE="$CASE_DIR/run_continue_t3.sbatch"
cat >"$SBATCH_FILE" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1
#SBATCH --job-name=mfc-a40-t3
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err

set -euo pipefail

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
MPI_LAUNCHER="$(command -v mpirun)"
if [[ -z "$MPI_LAUNCHER" || ! -x "$MPI_LAUNCHER" ]]; then
    echo "ERROR: mpirun was not found after loading openmpi/5.0.3" >&2
    exit 5
fi
echo "MPI_LAUNCHER=$MPI_LAUNCHER"
"$MPI_LAUNCHER" --version | head -2

cd "$MFC_ROOT"
mkdir -p build
exec 9>build/.mfc-a40-startup.lock
if command -v flock >/dev/null 2>&1; then
    flock -w 1800 9
fi

set +e
./mfc.sh run "$CASE_DIR/case_continue_t3.py" \
    -n "$SLURM_NTASKS" -j 1 --mpi --no-gpu --binary mpirun --no-build \
    -t pre_process simulation post_process \
    2>&1 | tee "$CASE_DIR/mfc-continue-t3.log"
mfc_status=${PIPESTATUS[0]}
set -e
if [[ "$mfc_status" -ne 0 ]]; then
    echo "MFC continuation failed with exit code $mfc_status" >&2
    exit "$mfc_status"
fi

grep -am1 'Number of 2D model boundary edges' "$CASE_DIR/mfc-continue-t3.log"
grep -aq 'Number of 2D model boundary edges: *4' "$CASE_DIR/mfc-continue-t3.log" || {
    echo "ERROR: MFC did not detect four airfoil boundary edges" >&2
    exit 3
}
[[ -s "$CASE_DIR/restart_data/lustre_${STOP_STEP}.dat" ]] || {
    echo "ERROR: final restart step $STOP_STEP was not written" >&2
    exit 3
}
touch "$CASE_DIR/CONTINUE_T3_OK.txt"

PACK_PY="$MFC_ROOT/build/venv/bin/python3"
if [[ ! -x "$PACK_PY" ]] || ! "$PACK_PY" -c 'import h5py,numpy' >/dev/null 2>&1; then
    echo "ERROR: MFC Python environment lacks h5py/numpy; raw result is valid." >&2
    exit 4
fi

"$PACK_PY" "$CASE_DIR/pack_startup_fields.py" "$CASE_DIR" \
    --output "$CASE_DIR/MFC_A40_STARTUP_T3_COMPACT.npz" \
    2>&1 | tee "$CASE_DIR/pack-startup-t3.log"

cat >"$CASE_DIR/UPLOAD_T3_INSTRUCTIONS.txt" <<'EOF'
Upload every MFC_A40_STARTUP_T3_*.part file plus PARTS.sha256 and
ORIGINAL.sha256 from upload_parts_t3/. Keep restart_data/ and silo_hdf5/ on
Unity until the longer-time movie and vortex/shock analysis are verified.
EOF

BUNDLE="$CASE_DIR/MFC_A40_STARTUP_T3_FOR_CHATGPT.tar"
tar -C "$CASE_DIR" -cf "$BUNDLE" \
    MFC_A40_STARTUP_T3_COMPACT.npz case.py case_continue_t3.py \
    Diamond_Airfoil_2D_MFC.stl RUN_OK.txt CONTINUE_T3_OK.txt \
    mfc-startup.log mfc-continue-t3.log pack-startup-t3.log \
    run_time.inf UPLOAD_T3_INSTRUCTIONS.txt

UPLOAD_DIR="$CASE_DIR/upload_parts_t3"
mkdir -p "$UPLOAD_DIR"
(
    cd "$CASE_DIR"
    sha256sum "$(basename "$BUNDLE")" >"$UPLOAD_DIR/ORIGINAL.sha256"
)
split -b 90M -d -a 3 "$BUNDLE" "$UPLOAD_DIR/MFC_A40_STARTUP_T3_"
for path in "$UPLOAD_DIR"/MFC_A40_STARTUP_T3_*; do
    mv "$path" "$path.part"
done
(
    cd "$UPLOAD_DIR"
    sha256sum MFC_A40_STARTUP_T3_*.part >PARTS.sha256
)

echo "CONTINUE_T3_OK=$CASE_DIR/CONTINUE_T3_OK.txt"
echo "UPLOAD_DIR=$UPLOAD_DIR"
du -sh "$CASE_DIR/restart_data" "$CASE_DIR/silo_hdf5" "$UPLOAD_DIR" 2>/dev/null || true
SBATCH

ENV_FILE="$SOURCE_RUN_BASE/submission_continue_t3.env"
cat >"$ENV_FILE" <<EOF
SOURCE_RUN_BASE=$SOURCE_RUN_BASE
CASE_DIR=$CASE_DIR
MFC_ROOT=$MFC_ROOT
START_STEP=$START_STEP
STOP_STEP=$STOP_STEP
SAVE_EVERY=$SAVE_EVERY
EOF

(
    cd "$CASE_DIR"
    RESTART_JOB="$(sbatch --parsable \
        --ntasks="$NTASKS" --mem="$MEMORY" --time="$WALLTIME" \
        --export=ALL,CASE_DIR="$CASE_DIR",MFC_ROOT="$MFC_ROOT",START_STEP="$START_STEP",STOP_STEP="$STOP_STEP" \
        "$SBATCH_FILE")"
    echo "RESTART_JOB=$RESTART_JOB" | tee -a "$ENV_FILE"
)

echo "SOURCE_RUN_BASE=$SOURCE_RUN_BASE"
echo "CASE_DIR=$CASE_DIR"
echo "ENV_FILE=$ENV_FILE"
grep '^RESTART_JOB=' "$ENV_FILE"
echo "STATUS: source '$ENV_FILE'; squeue -j \"\$RESTART_JOB\""
