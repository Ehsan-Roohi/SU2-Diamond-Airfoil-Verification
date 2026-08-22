#!/usr/bin/env bash
set -Eeuo pipefail

# Resume only the dt/4 gate after t=0..0.4 movies and field audit have passed.
# No post-processing or movie generation from the completed partial stage is repeated.

REPOSITORY=Ehsan-Roohi/SU2-Diamond-Airfoil-Verification
SOURCE_REF=dd8ee8d24905c487fe110c0ff93e16c0b65e9919
RAW_BASE="https://raw.githubusercontent.com/$REPOSITORY/$SOURCE_REF/mfc_iles_a40"
DATA_ROOT="${DATA_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}"
RECOVERY_ROOT="$DATA_ROOT/runs/mfc_iles_a40_recovery"
MFC_ILES_ROOT="${MFC_ILES_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
FPS="${FPS:-2}"

if [[ -z "${RUN_BASE:-}" ]]; then
    RUN_BASE="$(find "$RECOVERY_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -name 'f270_dt4_*' -printf '%T@\t%p\n' | sort -nr | \
        awk 'NR==1{sub(/^[^[:space:]]+[[:space:]]+/,""); print; exit}')"
fi
[[ -n "$RUN_BASE" && "$RUN_BASE" == "$RECOVERY_ROOT"/f270_dt4_* ]] || {
    echo "ERROR: RUN_BASE is missing or outside $RECOVERY_ROOT" >&2
    exit 2
}
[[ -s "$RUN_BASE/submission.env" ]] || {
    echo "ERROR: missing $RUN_BASE/submission.env" >&2
    exit 2
}
source "$RUN_BASE/submission.env"

for path_to_check in "$RUN_BASE" "$PARTIAL_DIR" "$GATE_DIR" "$PROD_DIR" \
    "$COMBINED_DIR" "$MFC_ILES_ROOT"; do
    [[ "$path_to_check" == /* && "$path_to_check" != / && -d "$path_to_check" ]] || {
        echo "ERROR: unsafe or missing directory: $path_to_check" >&2
        exit 2
    }
done
[[ -x "$MFC_ILES_ROOT/build/venv/bin/python3" ]] || {
    echo "ERROR: missing MFC Python environment under $MFC_ILES_ROOT" >&2
    exit 2
}
[[ -s "$RUN_BASE/run_recovery_stage.sbatch" ]] || {
    echo "ERROR: missing stage script in $RUN_BASE" >&2
    exit 2
}
[[ -s "$RUN_BASE/run_recovery_finalize.sbatch" ]] || {
    echo "ERROR: missing finalization script in $RUN_BASE" >&2
    exit 2
}

AUDIT="$PARTIAL_DIR/movie_products/mfc-iles-a40-partial-field-audit.json"
PARTIAL_ZIP="$PARTIAL_DIR/mfc-iles-a40-partial-movie-products.zip"
for product in "$AUDIT" "$PARTIAL_ZIP" \
    "$PARTIAL_DIR/movie_products/mfc-iles-a40-partial-vorticity-shedding.mp4" \
    "$PARTIAL_DIR/movie_products/mfc-iles-a40-partial-shock-formation.mp4" \
    "$PARTIAL_DIR/RUN_OK_PARTIAL_MOVIES_RESUMED.txt"; do
    [[ -s "$product" ]] || {
        echo "ERROR: completed partial-stage product is missing: $product" >&2
        exit 3
    }
done
python3 - "$AUDIT" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
audit = json.loads(path.read_text())
assert audit.get("pass") is True, f"partial field audit is not PASS: {path}"
assert len(audit.get("available_steps", [])) == 9, "expected nine t=0..0.4 fields"
PY

# Refresh the fixed restart case and the already-tested movie renderer in every
# downstream directory.  Existing checkpoint and movie products are untouched.
for destination in "$GATE_DIR" "$PROD_DIR" "$COMBINED_DIR"; do
    curl -fL --retry 3 "$RAW_BASE/case_iles_recovery.py" \
        -o "$destination/case.py.download"
    mv "$destination/case.py.download" "$destination/case.py"
    curl -fL --retry 3 "$RAW_BASE/make_recovery_movies.py" \
        -o "$destination/make_recovery_movies.py.download"
    mv "$destination/make_recovery_movies.py.download" \
        "$destination/make_recovery_movies.py"
done

for restart_file in \
    "$GATE_DIR/restart_data/lustre_8640.dat" \
    "$GATE_DIR/restart_data/ib_state_8640.dat" \
    "$GATE_DIR/restart_data/lustre_x_cb.dat" \
    "$GATE_DIR/restart_data/lustre_y_cb.dat"; do
    [[ -s "$restart_file" ]] || {
        echo "ERROR: gate restart input is missing: $restart_file" >&2
        exit 3
    }
done

# Fail on the login node before submission if the dt/4 restart clock or the
# explicit validator bounds regress.
python3 "$GATE_DIR/case.py" --mode restart --grid f270 --start-time 0.4 \
    --final-time 0.5 --save-dt 0.01 --dt-factor 4 --format binary | \
python3 -c 'import json,sys; c=json.load(sys.stdin); assert (c["t_step_start"],c["t_step_stop"],c["t_step_save"],c["t_step_old"])==(8640,10800,216,0); assert (c["x_domain%beg"],c["x_domain%end"],c["y_domain%beg"],c["y_domain%end"])==(-5.0,6.0,-5.0,5.0)'

GATE_JOB="$({
    cd "$GATE_DIR"
    sbatch --parsable --ntasks=32 --mem=96G --time=08:00:00 \
        --constraint='intel&x86_64_v4' --job-name=mfc-iles-dt4-gate-r2 \
        --output="$GATE_DIR/slurm-%j.out" --error="$GATE_DIR/slurm-%j.err" \
        --export="ALL,STAGE=gate,CASE_DIR=$GATE_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,START_TIME=0.4,STOP_TIME=0.5,SAVE_DT=0.01,DT_FACTOR=4,FPS=$FPS" \
        "$RUN_BASE/run_recovery_stage.sbatch"
})"
GATE_JOB="${GATE_JOB%%;*}"

PROD_JOB="$({
    cd "$PROD_DIR"
    sbatch --parsable --ntasks=32 --mem=96G --time=2-00:00:00 \
        --constraint='intel&x86_64_v4' --dependency="afterok:$GATE_JOB" \
        --job-name=mfc-iles-dt4-prod-r2 \
        --output="$PROD_DIR/slurm-%j.out" --error="$PROD_DIR/slurm-%j.err" \
        --export="ALL,STAGE=production,CASE_DIR=$PROD_DIR,PARENT_DIR=$GATE_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,START_TIME=0.5,STOP_TIME=3.0,SAVE_DT=0.02,DT_FACTOR=4,FPS=$FPS" \
        "$RUN_BASE/run_recovery_stage.sbatch"
})"
PROD_JOB="${PROD_JOB%%;*}"

FINAL_JOB="$({
    cd "$COMBINED_DIR"
    sbatch --parsable --ntasks=1 --mem=96G --time=08:00:00 \
        --constraint='intel&x86_64_v4' --dependency="afterok:$PROD_JOB" \
        --job-name=mfc-iles-final-movies-r2 \
        --output="$COMBINED_DIR/slurm-%j.out" --error="$COMBINED_DIR/slurm-%j.err" \
        --export="ALL,PARTIAL_DIR=$PARTIAL_DIR,GATE_DIR=$GATE_DIR,PROD_DIR=$PROD_DIR,COMBINED_DIR=$COMBINED_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,FPS=$FPS" \
        "$RUN_BASE/run_recovery_finalize.sbatch"
})"
FINAL_JOB="${FINAL_JOB%%;*}"

for job in "$GATE_JOB" "$PROD_JOB" "$FINAL_JOB"; do
    [[ "$job" =~ ^[0-9]+$ ]] || {
        echo "ERROR: invalid Slurm job id: $job" >&2
        exit 4
    }
done

RESUME_ENV="$RUN_BASE/gate_resume.env"
{
    printf 'RUN_BASE=%q\n' "$RUN_BASE"
    printf 'GATE_JOB=%q\n' "$GATE_JOB"
    printf 'PROD_JOB=%q\n' "$PROD_JOB"
    printf 'FINAL_JOB=%q\n' "$FINAL_JOB"
    printf 'JOBS=%q\n' "$GATE_JOB,$PROD_JOB,$FINAL_JOB"
} >"$RESUME_ENV"

echo "RUN_BASE=$RUN_BASE"
echo "PARTIAL_PRODUCTS=REUSED_AND_VERIFIED"
echo "GATE_JOB=$GATE_JOB (dt/4, t=0.4..0.5)"
echo "PROD_JOB=$PROD_JOB (afterok gate, t=0.5..3)"
echo "FINAL_JOB=$FINAL_JOB (afterok production, combined movies)"
echo "RESUME_ENV=$RESUME_ENV"
echo "STATUS: source '$RESUME_ENV'; squeue -j \"\$JOBS\""
