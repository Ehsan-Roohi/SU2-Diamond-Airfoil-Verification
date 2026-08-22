#!/usr/bin/env bash
set -Eeuo pipefail

# Recover the valid t<=0.4 fields, cross the failed point with dt/4, and only
# then continue to t=3.  All scientific stages are chained with afterok.

REPOSITORY=Ehsan-Roohi/SU2-Diamond-Airfoil-Verification
SOURCE_REF=agent/mfc-a40-iles-screen
RAW_BASE="https://raw.githubusercontent.com/$REPOSITORY/$SOURCE_REF/mfc_iles_a40"
DATA_ROOT="${DATA_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}"
MFC_ILES_ROOT="${MFC_ILES_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
SOURCE_CASE_DIR="${SOURCE_CASE_DIR:-$DATA_ROOT/runs/mfc_iles_a40/f270_t3_20260821-100843/f270}"
GRID=f270
SOURCE_SAFE_TIME=0.4
GATE_FINAL_TIME=0.5
FINAL_TIME=3.0
DT_FACTOR=4
FPS="${FPS:-2}"

for path in "$DATA_ROOT" "$MFC_ILES_ROOT" "$SOURCE_CASE_DIR"; do
    [[ "$path" == /* && "$path" != / ]] || {
        echo "ERROR: unsafe or non-absolute path: $path" >&2
        exit 2
    }
done
[[ -x "$MFC_ILES_ROOT/mfc.sh" ]] || {
    echo "ERROR: portable MFC installation is missing: $MFC_ILES_ROOT" >&2
    exit 2
}
[[ -x "$MFC_ILES_ROOT/build/venv/bin/python3" ]] || {
    echo "ERROR: MFC Python environment is missing; the successful build should have created it." >&2
    exit 2
}
SOURCE_RESTART="$SOURCE_CASE_DIR/restart_data"
[[ -d "$SOURCE_RESTART" ]] || { echo "ERROR: missing $SOURCE_RESTART" >&2; exit 2; }

SOURCE_STEP=2160
SOURCE_STATE="$SOURCE_RESTART/lustre_${SOURCE_STEP}.dat"
SOURCE_IB_STATE="$SOURCE_RESTART/ib_state_${SOURCE_STEP}.dat"
for file in "$SOURCE_STATE" "$SOURCE_IB_STATE" \
    "$SOURCE_RESTART/lustre_x_cb.dat" "$SOURCE_RESTART/lustre_y_cb.dat" \
    "$SOURCE_RESTART/lustre_ib.dat"; do
    [[ -s "$file" ]] || { echo "ERROR: required recovery input is missing: $file" >&2; exit 3; }
done

# The state files should all have the same byte count.  A truncated final
# checkpoint must never be selected merely because its name is the largest.
REFERENCE_SIZE="$(stat -c %s "$SOURCE_RESTART/lustre_1890.dat")"
[[ "$(stat -c %s "$SOURCE_STATE")" == "$REFERENCE_SIZE" ]] || {
    echo "ERROR: checkpoint $SOURCE_STATE has a different size from step 1890." >&2
    exit 3
}

STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_BASE="$DATA_ROOT/runs/mfc_iles_a40_recovery/f270_dt4_${STAMP}"
PARTIAL_DIR="$RUN_BASE/partial_t000_t0400"
GATE_DIR="$RUN_BASE/gate_t0400_t0500"
PROD_DIR="$RUN_BASE/production_t0500_t3000"
COMBINED_DIR="$RUN_BASE/combined_t000_t3000"
mkdir -p "$PARTIAL_DIR/restart_data" "$GATE_DIR/restart_data" \
    "$PROD_DIR/restart_data" "$COMBINED_DIR"

for destination in "$PARTIAL_DIR" "$GATE_DIR" "$PROD_DIR" "$COMBINED_DIR"; do
    curl -fL --retry 3 "$RAW_BASE/case_iles_recovery.py" -o "$destination/case.py"
    curl -fL --retry 3 "$RAW_BASE/make_recovery_movies.py" -o "$destination/make_recovery_movies.py"
    curl -fL --retry 3 "$RAW_BASE/Diamond_Airfoil_2D_MFC.stl" \
        -o "$destination/Diamond_Airfoil_2D_MFC.stl"
done

# Partial post-processing uses read-only links to every complete original
# checkpoint through t=0.4.  The failed source directory is not modified.
for ((step=0; step<=SOURCE_STEP; step+=270)); do
    file="$SOURCE_RESTART/lustre_${step}.dat"
    [[ -s "$file" ]] || { echo "ERROR: missing original checkpoint $file" >&2; exit 3; }
    ln -s "$file" "$PARTIAL_DIR/restart_data/lustre_${step}.dat"
done
for name in lustre_x_cb.dat lustre_y_cb.dat lustre_ib.dat; do
    ln -s "$SOURCE_RESTART/$name" "$PARTIAL_DIR/restart_data/$name"
done
for file in "$SOURCE_RESTART"/ib_state_*.dat; do
    [[ -e "$file" ]] || continue
    ln -s "$file" "$PARTIAL_DIR/restart_data/$(basename "$file")"
done

# Re-index t=0.4 from the old 1/5400 clock to the dt/4 clock.  MFC restart
# files contain field arrays; the filename selects the starting index.
GATE_START_STEP=8640
cp --reflink=auto "$SOURCE_STATE" "$GATE_DIR/restart_data/lustre_${GATE_START_STEP}.dat"
cp --reflink=auto "$SOURCE_IB_STATE" "$GATE_DIR/restart_data/ib_state_${GATE_START_STEP}.dat"
cp --reflink=auto "$SOURCE_RESTART/lustre_x_cb.dat" "$GATE_DIR/restart_data/lustre_x_cb.dat"
cp --reflink=auto "$SOURCE_RESTART/lustre_y_cb.dat" "$GATE_DIR/restart_data/lustre_y_cb.dat"

CASE_SHA256="$(sha256sum "$PARTIAL_DIR/case.py" | awk '{print $1}')"
MOVIE_SHA256="$(sha256sum "$PARTIAL_DIR/make_recovery_movies.py" | awk '{print $1}')"
STL_SHA256="$(sha256sum "$PARTIAL_DIR/Diamond_Airfoil_2D_MFC.stl" | awk '{print $1}')"
for destination in "$GATE_DIR" "$PROD_DIR" "$COMBINED_DIR"; do
    [[ "$(sha256sum "$destination/case.py" | awk '{print $1}')" == "$CASE_SHA256" ]]
    [[ "$(sha256sum "$destination/make_recovery_movies.py" | awk '{print $1}')" == "$MOVIE_SHA256" ]]
    [[ "$(sha256sum "$destination/Diamond_Airfoil_2D_MFC.stl" | awk '{print $1}')" == "$STL_SHA256" ]]
done

# Local preflight verifies the no-argument profile used by `mfc.sh validate`,
# both clocks, and the documented MFC restart form.
python3 "$PARTIAL_DIR/case.py" | \
    python3 -c 'import json,sys; c=json.load(sys.stdin); assert (c["t_step_start"],c["t_step_stop"],c["t_step_save"],c["format"])==(0,2160,270,2)'
python3 "$PARTIAL_DIR/case.py" --mode initial --grid f270 --start-time 0 \
    --final-time 0.4 --save-dt 0.05 --dt-factor 1 --format binary | \
    python3 -c 'import json,sys; c=json.load(sys.stdin); assert (c["t_step_start"],c["t_step_stop"],c["t_step_save"],c["format"])==(0,2160,270,2)'
python3 "$GATE_DIR/case.py" --mode restart --grid f270 --start-time 0.4 \
    --final-time 0.5 --save-dt 0.01 --dt-factor 4 --format binary | \
    python3 -c 'import json,sys; c=json.load(sys.stdin); assert (c["t_step_start"],c["t_step_stop"],c["t_step_save"],c["num_patches"],c["old_ic"],c["old_grid"])==(8640,10800,216,0,"T","T")'

STAGE_SBATCH="$RUN_BASE/run_recovery_stage.sbatch"
cat >"$STAGE_SBATCH" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1
set -Eeuo pipefail

: "${STAGE:?}"
: "${CASE_DIR:?}"
: "${MFC_ILES_ROOT:?}"
: "${START_TIME:?}"
: "${STOP_TIME:?}"
: "${SAVE_DT:?}"
: "${DT_FACTOR:?}"
: "${FPS:?}"

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
MFC_PYTHON="$MFC_ILES_ROOT/build/venv/bin/python3"
[[ -x "$MFC_PYTHON" ]] || { echo "ERROR: missing $MFC_PYTHON" >&2; exit 5; }
[[ "$(grep -c 'facet normal' "$CASE_DIR/Diamond_Airfoil_2D_MFC.stl")" -eq 2 ]] || {
    echo "ERROR: the diamond STL must contain exactly two facets." >&2
    exit 5
}

if [[ "$STAGE" == partial ]]; then
    MODE=initial
else
    MODE=restart
fi

if [[ "$STAGE" == production ]]; then
    : "${PARENT_DIR:?}"
    parent_step=10800
    start_step=10800
    for source_name in "lustre_${parent_step}.dat" "ib_state_${parent_step}.dat"; do
        source="$PARENT_DIR/restart_data/$source_name"
        [[ -s "$source" ]] || { echo "ERROR: missing gate output $source" >&2; exit 6; }
        cp --reflink=auto "$source" "$CASE_DIR/restart_data/${source_name/$parent_step/$start_step}"
    done
    for name in lustre_x_cb.dat lustre_y_cb.dat; do
        cp --reflink=auto "$PARENT_DIR/restart_data/$name" "$CASE_DIR/restart_data/$name"
    done
fi

CASE_ARGS=(--mode "$MODE" --grid f270 --start-time "$START_TIME" \
    --final-time "$STOP_TIME" --save-dt "$SAVE_DT" --dt-factor "$DT_FACTOR" \
    --format binary)
cd "$MFC_ILES_ROOT"
LOCK_FILE="$MFC_ILES_ROOT/build/.mfc-iles-a40.lock"
if command -v flock >/dev/null 2>&1 && [[ "${MFC_LOCK_HELD:-0}" != 1 ]]; then
    export MFC_LOCK_HELD=1
    exec flock -s -w 7200 "$LOCK_FILE" bash "$0"
fi

./mfc.sh validate "$CASE_DIR/case.py" -- "${CASE_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/validate-${STAGE}.log"

simulation_status=0
if [[ "$STAGE" != partial ]]; then
    ./mfc.sh run "$CASE_DIR/case.py" -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" \
        --mpi --no-gpu --binary mpirun --no-build -t pre_process -- "${CASE_ARGS[@]}" \
        2>&1 | tee "$CASE_DIR/pre-${STAGE}.log"

    set +e
    ./mfc.sh run "$CASE_DIR/case.py" -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" \
        --mpi --no-gpu --binary mpirun --no-build -t simulation -- "${CASE_ARGS[@]}" \
        2>&1 | tee "$CASE_DIR/simulation-${STAGE}.log"
    simulation_status=${PIPESTATUS[0]}
    set -e
fi

read -r nominal_start nominal_stop nominal_save dt_value < <(
    "$MFC_PYTHON" "$CASE_DIR/case.py" "${CASE_ARGS[@]}" | \
    "$MFC_PYTHON" -c 'import json,sys; c=json.load(sys.stdin); print(c["t_step_start"],c["t_step_stop"],c["t_step_save"],repr(c["dt"]))'
)

if [[ "$STAGE" == partial ]]; then
    latest_step=$nominal_stop
else
    latest_step="$nominal_start"
    while IFS= read -r file; do
        base="$(basename "$file")"
        step="${base#lustre_}"
        step="${step%.dat}"
        [[ "$step" =~ ^[0-9]+$ ]] || continue
        if (( step >= nominal_start && step <= nominal_stop && (step - nominal_start) % nominal_save == 0 && step > latest_step )); then
            latest_step=$step
        fi
    done < <(find "$CASE_DIR/restart_data" -maxdepth 1 -type f -name 'lustre_[0-9]*.dat' -print)
fi

latest_time="$($MFC_PYTHON -c 'import sys; print(int(sys.argv[1])*float(sys.argv[2]))' "$latest_step" "$dt_value")"
PP_ARGS=(--mode "$MODE" --grid f270 --start-time "$START_TIME" \
    --final-time "$latest_time" --save-dt "$SAVE_DT" --dt-factor "$DT_FACTOR" \
    --format binary)

./mfc.sh run "$CASE_DIR/case.py" -n "$SLURM_NTASKS" -j "$SLURM_NTASKS" \
    --mpi --no-gpu --binary mpirun --no-build -t post_process -- "${PP_ARGS[@]}" \
    2>&1 | tee "$CASE_DIR/post-${STAGE}.log"

PYTHONPATH="$MFC_ILES_ROOT/toolchain${PYTHONPATH:+:$PYTHONPATH}" \
    "$MFC_PYTHON" "$CASE_DIR/make_recovery_movies.py" \
    --case-dir "$CASE_DIR" --mfc-root "$MFC_ILES_ROOT" --dt "$dt_value" \
    --label "mfc-iles-a40-${STAGE}" --fps "$FPS" \
    2>&1 | tee "$CASE_DIR/movie-${STAGE}.log"

product_zip="$CASE_DIR/mfc-iles-a40-${STAGE}-movie-products.zip"
"$MFC_PYTHON" - "$CASE_DIR/movie_products" "$product_zip" <<'PY'
from pathlib import Path
import sys, zipfile
source, output = map(Path, sys.argv[1:])
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(source.iterdir()):
        if path.is_file():
            archive.write(path, path.name)
PY
sha256sum "$product_zip" >"$product_zip.sha256.txt"

if (( simulation_status != 0 )); then
    printf 'status=SIMULATION_FAILED\nstage=%s\nlatest_step=%s\nlatest_time=%s\nproducts=%s\n' \
        "$STAGE" "$latest_step" "$latest_time" "$product_zip" | \
        tee "$CASE_DIR/RUN_FAILED_BUT_FIELDS_RECOVERED.txt"
    exit "$simulation_status"
fi
if (( latest_step != nominal_stop )); then
    echo "ERROR: $STAGE ended at checkpoint $latest_step, expected $nominal_stop." >&2
    exit 45
fi
printf 'status=PASS\nstage=%s\nstart_time=%s\nstop_time=%s\nlatest_step=%s\nproducts=%s\n' \
    "$STAGE" "$START_TIME" "$STOP_TIME" "$latest_step" "$product_zip" | \
    tee "$CASE_DIR/RUN_OK_${STAGE^^}.txt"
SBATCH

FINAL_SBATCH="$RUN_BASE/run_recovery_finalize.sbatch"
cat >"$FINAL_SBATCH" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1
set -Eeuo pipefail
: "${PARTIAL_DIR:?}"
: "${GATE_DIR:?}"
: "${PROD_DIR:?}"
: "${COMBINED_DIR:?}"
: "${MFC_ILES_ROOT:?}"
: "${FPS:?}"

module purge
module load openmpi/5.0.3
MFC_PYTHON="$MFC_ILES_ROOT/build/venv/bin/python3"
mkdir -p "$COMBINED_DIR/binary"
for rank_dir in "$PROD_DIR"/binary/p*; do
    [[ -d "$rank_dir" ]] || continue
    rank="$(basename "$rank_dir")"
    mkdir -p "$COMBINED_DIR/binary/$rank"
done

# Original dt is four times the recovery dt.  Re-index original binary fields
# so every combined filename obeys physical_time = step / 21600.
for rank_dir in "$PARTIAL_DIR"/binary/p*; do
    [[ -d "$rank_dir" ]] || continue
    rank="$(basename "$rank_dir")"
    for file in "$rank_dir"/*.dat; do
        old="$(basename "$file" .dat)"
        [[ "$old" =~ ^[0-9]+$ ]] || continue
        new=$((old * 4))
        (( new < 8640 )) || continue
        ln -s "$file" "$COMBINED_DIR/binary/$rank/$new.dat"
    done
done
for source_dir in "$GATE_DIR" "$PROD_DIR"; do
    for rank_dir in "$source_dir"/binary/p*; do
        [[ -d "$rank_dir" ]] || continue
        rank="$(basename "$rank_dir")"
        for file in "$rank_dir"/*.dat; do
            step="$(basename "$file" .dat)"
            [[ "$step" =~ ^[0-9]+$ ]] || continue
            target="$COMBINED_DIR/binary/$rank/$step.dat"
            [[ -e "$target" || -L "$target" ]] || ln -s "$file" "$target"
        done
    done
done

PYTHONPATH="$MFC_ILES_ROOT/toolchain${PYTHONPATH:+:$PYTHONPATH}" \
    "$MFC_PYTHON" "$COMBINED_DIR/make_recovery_movies.py" \
    --case-dir "$COMBINED_DIR" --mfc-root "$MFC_ILES_ROOT" --dt "$(python3 -c 'print(1/21600)')" \
    --label mfc-iles-a40-t0-to-t3 --fps "$FPS" \
    2>&1 | tee "$COMBINED_DIR/movie-combined.log"

product_zip="$COMBINED_DIR/MFC_ILES_A40_T0_TO_T3_MOVIES.zip"
"$MFC_PYTHON" - "$COMBINED_DIR/movie_products" "$product_zip" <<'PY'
from pathlib import Path
import sys, zipfile
source, output = map(Path, sys.argv[1:])
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(source.iterdir()):
        if path.is_file():
            archive.write(path, path.name)
PY
sha256sum "$product_zip" >"$product_zip.sha256.txt"
printf 'status=PASS\ncombined_products=%s\n' "$product_zip" | \
    tee "$COMBINED_DIR/RUN_OK_COMBINED_MOVIES.txt"
SBATCH

PARTIAL_JOB="$({
    cd "$PARTIAL_DIR"
    sbatch --parsable --ntasks=32 --mem=96G --time=06:00:00 \
        --constraint='intel&x86_64_v4' --job-name=mfc-iles-recover-fields \
        --output="$PARTIAL_DIR/slurm-%j.out" --error="$PARTIAL_DIR/slurm-%j.err" \
        --export="ALL,STAGE=partial,CASE_DIR=$PARTIAL_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,START_TIME=0,STOP_TIME=0.4,SAVE_DT=0.05,DT_FACTOR=1,FPS=$FPS" \
        "$STAGE_SBATCH"
})"
PARTIAL_JOB="${PARTIAL_JOB%%;*}"

GATE_JOB="$({
    cd "$GATE_DIR"
    sbatch --parsable --ntasks=32 --mem=96G --time=08:00:00 \
        --constraint='intel&x86_64_v4' --dependency="afterok:$PARTIAL_JOB" \
        --job-name=mfc-iles-dt4-gate \
        --output="$GATE_DIR/slurm-%j.out" --error="$GATE_DIR/slurm-%j.err" \
        --export="ALL,STAGE=gate,CASE_DIR=$GATE_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,START_TIME=0.4,STOP_TIME=0.5,SAVE_DT=0.01,DT_FACTOR=4,FPS=$FPS" \
        "$STAGE_SBATCH"
})"
GATE_JOB="${GATE_JOB%%;*}"

PROD_JOB="$({
    cd "$PROD_DIR"
    sbatch --parsable --ntasks=32 --mem=96G --time=2-00:00:00 \
        --constraint='intel&x86_64_v4' --dependency="afterok:$GATE_JOB" \
        --job-name=mfc-iles-dt4-prod \
        --output="$PROD_DIR/slurm-%j.out" --error="$PROD_DIR/slurm-%j.err" \
        --export="ALL,STAGE=production,CASE_DIR=$PROD_DIR,PARENT_DIR=$GATE_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,START_TIME=0.5,STOP_TIME=3.0,SAVE_DT=0.02,DT_FACTOR=4,FPS=$FPS" \
        "$STAGE_SBATCH"
})"
PROD_JOB="${PROD_JOB%%;*}"

FINAL_JOB="$({
    cd "$COMBINED_DIR"
    sbatch --parsable --ntasks=1 --mem=96G --time=08:00:00 \
        --constraint='intel&x86_64_v4' --dependency="afterok:$PROD_JOB" \
        --job-name=mfc-iles-final-movies \
        --output="$COMBINED_DIR/slurm-%j.out" --error="$COMBINED_DIR/slurm-%j.err" \
        --export="ALL,PARTIAL_DIR=$PARTIAL_DIR,GATE_DIR=$GATE_DIR,PROD_DIR=$PROD_DIR,COMBINED_DIR=$COMBINED_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,FPS=$FPS" \
        "$FINAL_SBATCH"
})"
FINAL_JOB="${FINAL_JOB%%;*}"

for job in "$PARTIAL_JOB" "$GATE_JOB" "$PROD_JOB" "$FINAL_JOB"; do
    [[ "$job" =~ ^[0-9]+$ ]] || { echo "ERROR: invalid Slurm job id: $job" >&2; exit 4; }
done

ENV_FILE="$RUN_BASE/submission.env"
{
    printf 'RUN_BASE=%q\n' "$RUN_BASE"
    printf 'SOURCE_CASE_DIR=%q\n' "$SOURCE_CASE_DIR"
    printf 'PARTIAL_DIR=%q\n' "$PARTIAL_DIR"
    printf 'GATE_DIR=%q\n' "$GATE_DIR"
    printf 'PROD_DIR=%q\n' "$PROD_DIR"
    printf 'COMBINED_DIR=%q\n' "$COMBINED_DIR"
    printf 'PARTIAL_JOB=%q\n' "$PARTIAL_JOB"
    printf 'GATE_JOB=%q\n' "$GATE_JOB"
    printf 'PROD_JOB=%q\n' "$PROD_JOB"
    printf 'FINAL_JOB=%q\n' "$FINAL_JOB"
    printf 'JOBS=%q\n' "$PARTIAL_JOB,$GATE_JOB,$PROD_JOB,$FINAL_JOB"
} >"$ENV_FILE"

echo "RUN_BASE=$RUN_BASE"
echo "PARTIAL_JOB=$PARTIAL_JOB (recover t=0..0.4 fields + movies + health audit)"
echo "GATE_JOB=$GATE_JOB (afterok partial; dt/4, t=0.4..0.5)"
echo "PROD_JOB=$PROD_JOB (afterok gate; dt/4, t=0.5..3)"
echo "FINAL_JOB=$FINAL_JOB (afterok production; fixed-scale combined movies)"
echo "ENV_FILE=$ENV_FILE"
echo "STATUS: source '$ENV_FILE'; squeue -j \"\$JOBS\""
