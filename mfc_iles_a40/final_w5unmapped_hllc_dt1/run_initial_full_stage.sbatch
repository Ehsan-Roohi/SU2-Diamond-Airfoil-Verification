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

MODE=initial

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
