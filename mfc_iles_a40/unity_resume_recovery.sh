#!/usr/bin/env bash
set -Eeuo pipefail

# Reuse an already post-processed t=0..0.4 recovery directory after a
# visualization-only failure, then resume the gated dt/4 calculation.

REPOSITORY=Ehsan-Roohi/SU2-Diamond-Airfoil-Verification
SOURCE_REF=3e1d3e3d8299c340e58f891ecdd1330a55689968
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
find "$PARTIAL_DIR/binary" -type f -name '*.dat' -print -quit 2>/dev/null | grep -q . || {
    echo "ERROR: the successful t=0..0.4 binary post-processing output is absent." >&2
    exit 3
}

# Atomically refresh only the Python files.  Existing binary/restart fields are
# kept exactly as written by the successful post-processing job.
for destination in "$PARTIAL_DIR" "$GATE_DIR" "$PROD_DIR" "$COMBINED_DIR"; do
    curl -fL --retry 3 "$RAW_BASE/make_recovery_movies.py" \
        -o "$destination/make_recovery_movies.py.download"
    mv "$destination/make_recovery_movies.py.download" \
        "$destination/make_recovery_movies.py"
    curl -fL --retry 3 "$RAW_BASE/case_iles_recovery.py" \
        -o "$destination/case.py.download"
    mv "$destination/case.py.download" "$destination/case.py"
done

MOVIE_SBATCH="$RUN_BASE/run_partial_movie_resume.sbatch"
cat >"$MOVIE_SBATCH" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1
set -Eeuo pipefail
: "${PARTIAL_DIR:?}"
: "${MFC_ILES_ROOT:?}"
: "${FPS:?}"

module purge
module load openmpi/5.0.3
MFC_PYTHON="$MFC_ILES_ROOT/build/venv/bin/python3"
PYTHONPATH="$MFC_ILES_ROOT/toolchain${PYTHONPATH:+:$PYTHONPATH}" \
    "$MFC_PYTHON" "$PARTIAL_DIR/make_recovery_movies.py" \
    --case-dir "$PARTIAL_DIR" --mfc-root "$MFC_ILES_ROOT" \
    --dt 0.0001851851851851852 --label mfc-iles-a40-partial --fps "$FPS" \
    2>&1 | tee "$PARTIAL_DIR/movie-partial-resume.log"

product_zip="$PARTIAL_DIR/mfc-iles-a40-partial-movie-products.zip"
"$MFC_PYTHON" - "$PARTIAL_DIR/movie_products" "$product_zip" <<'PY'
from pathlib import Path
import sys, zipfile
source, output = map(Path, sys.argv[1:])
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(source.iterdir()):
        if path.is_file():
            archive.write(path, path.name)
PY
sha256sum "$product_zip" >"$product_zip.sha256.txt"
printf 'status=PASS\nstage=partial_movie_resume\nproducts=%s\n' "$product_zip" | \
    tee "$PARTIAL_DIR/RUN_OK_PARTIAL_MOVIES_RESUMED.txt"
SBATCH

MOVIE_JOB="$({
    cd "$PARTIAL_DIR"
    sbatch --parsable --ntasks=1 --mem=96G --time=04:00:00 \
        --constraint='intel&x86_64_v4' --job-name=mfc-iles-movie-fix \
        --output="$PARTIAL_DIR/slurm-%j.out" --error="$PARTIAL_DIR/slurm-%j.err" \
        --export="ALL,PARTIAL_DIR=$PARTIAL_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,FPS=$FPS" \
        "$MOVIE_SBATCH"
})"
MOVIE_JOB="${MOVIE_JOB%%;*}"

GATE_JOB="$({
    cd "$GATE_DIR"
    sbatch --parsable --ntasks=32 --mem=96G --time=08:00:00 \
        --constraint='intel&x86_64_v4' --dependency="afterok:$MOVIE_JOB" \
        --job-name=mfc-iles-dt4-gate-r1 \
        --output="$GATE_DIR/slurm-%j.out" --error="$GATE_DIR/slurm-%j.err" \
        --export="ALL,STAGE=gate,CASE_DIR=$GATE_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,START_TIME=0.4,STOP_TIME=0.5,SAVE_DT=0.01,DT_FACTOR=4,FPS=$FPS" \
        "$RUN_BASE/run_recovery_stage.sbatch"
})"
GATE_JOB="${GATE_JOB%%;*}"

PROD_JOB="$({
    cd "$PROD_DIR"
    sbatch --parsable --ntasks=32 --mem=96G --time=2-00:00:00 \
        --constraint='intel&x86_64_v4' --dependency="afterok:$GATE_JOB" \
        --job-name=mfc-iles-dt4-prod-r1 \
        --output="$PROD_DIR/slurm-%j.out" --error="$PROD_DIR/slurm-%j.err" \
        --export="ALL,STAGE=production,CASE_DIR=$PROD_DIR,PARENT_DIR=$GATE_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,START_TIME=0.5,STOP_TIME=3.0,SAVE_DT=0.02,DT_FACTOR=4,FPS=$FPS" \
        "$RUN_BASE/run_recovery_stage.sbatch"
})"
PROD_JOB="${PROD_JOB%%;*}"

FINAL_JOB="$({
    cd "$COMBINED_DIR"
    sbatch --parsable --ntasks=1 --mem=96G --time=08:00:00 \
        --constraint='intel&x86_64_v4' --dependency="afterok:$PROD_JOB" \
        --job-name=mfc-iles-final-movies-r1 \
        --output="$COMBINED_DIR/slurm-%j.out" --error="$COMBINED_DIR/slurm-%j.err" \
        --export="ALL,PARTIAL_DIR=$PARTIAL_DIR,GATE_DIR=$GATE_DIR,PROD_DIR=$PROD_DIR,COMBINED_DIR=$COMBINED_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,FPS=$FPS" \
        "$RUN_BASE/run_recovery_finalize.sbatch"
})"
FINAL_JOB="${FINAL_JOB%%;*}"

for job in "$MOVIE_JOB" "$GATE_JOB" "$PROD_JOB" "$FINAL_JOB"; do
    [[ "$job" =~ ^[0-9]+$ ]] || {
        echo "ERROR: invalid Slurm job id: $job" >&2
        exit 4
    }
done

RESUME_ENV="$RUN_BASE/resume.env"
{
    printf 'RUN_BASE=%q\n' "$RUN_BASE"
    printf 'MOVIE_JOB=%q\n' "$MOVIE_JOB"
    printf 'GATE_JOB=%q\n' "$GATE_JOB"
    printf 'PROD_JOB=%q\n' "$PROD_JOB"
    printf 'FINAL_JOB=%q\n' "$FINAL_JOB"
    printf 'JOBS=%q\n' "$MOVIE_JOB,$GATE_JOB,$PROD_JOB,$FINAL_JOB"
} >"$RESUME_ENV"

echo "RUN_BASE=$RUN_BASE"
echo "REUSED_PARTIAL_BINARY=$PARTIAL_DIR/binary"
echo "MOVIE_JOB=$MOVIE_JOB (existing t=0..0.4 fields; no CFD rerun)"
echo "GATE_JOB=$GATE_JOB (afterok movie; dt/4, t=0.4..0.5)"
echo "PROD_JOB=$PROD_JOB (afterok gate; dt/4, t=0.5..3)"
echo "FINAL_JOB=$FINAL_JOB (afterok production; combined fixed-scale movies)"
echo "RESUME_ENV=$RESUME_ENV"
echo "STATUS: source '$RESUME_ENV'; squeue -j \"\$JOBS\""
