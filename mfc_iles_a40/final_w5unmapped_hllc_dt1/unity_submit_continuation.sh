#!/usr/bin/env bash
set -Eeuo pipefail

: "${ILES_REF:?Set ILES_REF to the pinned GitHub commit containing this script.}"

REPOSITORY=Ehsan-Roohi/SU2-Diamond-Airfoil-Verification
CASE_SUBDIR=mfc_iles_a40/final_w5unmapped_hllc_dt1
RAW_BASE="https://raw.githubusercontent.com/$REPOSITORY/$ILES_REF/$CASE_SUBDIR"
DATA_ROOT="${DATA_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}"
SOURCE_CASE_DIR="${SOURCE_CASE_DIR:-$DATA_ROOT/runs/mfc_iles_a40_recovery/f270_dt4_20260822-194723/final_t000_t3000_w5unmapped_hllc_dt1}"
RUN_ROOT="${RUN_ROOT:-$DATA_ROOT/runs/mfc_iles_a40_continuation}"
MFC_ILES_ROOT="${MFC_ILES_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
START_TIME="${START_TIME:-3.0}"
STOP_TIME="${STOP_TIME:-6.0}"
SAVE_DT="${SAVE_DT:-0.05}"
DT_FACTOR="${DT_FACTOR:-1}"
FPS="${FPS:-2}"

[[ "$SOURCE_CASE_DIR" == /* && -d "$SOURCE_CASE_DIR" ]] || {
    echo "ERROR: unsafe or missing SOURCE_CASE_DIR: $SOURCE_CASE_DIR" >&2
    exit 2
}
[[ "$RUN_ROOT" == /* && "$RUN_ROOT" != / ]] || {
    echo "ERROR: unsafe RUN_ROOT: $RUN_ROOT" >&2
    exit 2
}
[[ -x "$MFC_ILES_ROOT/build/venv/bin/python3" ]] || {
    echo "ERROR: missing prepared MFC tree: $MFC_ILES_ROOT" >&2
    exit 2
}
[[ "$DT_FACTOR" == 1 ]] || {
    echo "ERROR: this continuation must retain the verified DT_FACTOR=1 clock." >&2
    exit 2
}

timestamp="$(date +%Y%m%d-%H%M%S)"
CASE_DIR="$RUN_ROOT/f270_t${START_TIME//./}_t${STOP_TIME//./}_w5unmapped_hllc_dt1_$timestamp"
[[ ! -e "$CASE_DIR" ]] || {
    echo "ERROR: destination already exists: $CASE_DIR" >&2
    exit 3
}
mkdir -p "$CASE_DIR/restart_data"

for name in case.py Diamond_Airfoil_2D_MFC.stl make_recovery_movies.py \
    run_restart_stage.sbatch SHA256SUMS.txt; do
    curl -fL --retry 3 "$RAW_BASE/$name" -o "$CASE_DIR/$name.download"
    mv "$CASE_DIR/$name.download" "$CASE_DIR/$name"
done
chmod u+x "$CASE_DIR/case.py" "$CASE_DIR/make_recovery_movies.py" \
    "$CASE_DIR/run_restart_stage.sbatch"

(
    cd "$CASE_DIR"
    awk '$2 == "case.py" || $2 == "Diamond_Airfoil_2D_MFC.stl" || $2 == "make_recovery_movies.py"' \
        SHA256SUMS.txt >CORE_SHA256SUMS.txt
    [[ "$(wc -l <CORE_SHA256SUMS.txt)" -eq 3 ]] || {
        echo "ERROR: incomplete core checksum list." >&2
        exit 4
    }
    sha256sum -c CORE_SHA256SUMS.txt
)

preflight="$CASE_DIR/preflight-restart.json"
python3 "$CASE_DIR/case.py" --mode restart --grid f270 \
    --start-time "$START_TIME" --final-time "$STOP_TIME" \
    --save-dt "$SAVE_DT" --dt-factor "$DT_FACTOR" --format binary \
    >"$preflight"

read -r start_step stop_step save_step dt_value < <(
    python3 - "$preflight" <<'PY'
import json
import sys

case = json.load(open(sys.argv[1]))
assert case["mapped_weno"] == "F"
assert case["weno_order"] == 5
assert case["riemann_solver"] == 2
assert case["wave_speeds"] == 1
assert case["ib_neighborhood_radius"] == 4
assert case["old_ic"] == "T" and case["old_grid"] == "T"
print(case["t_step_start"], case["t_step_stop"], case["t_step_save"], repr(case["dt"]))
PY
)

for name in "lustre_${start_step}.dat" "ib_state_${start_step}.dat" \
    lustre_x_cb.dat lustre_y_cb.dat; do
    source="$SOURCE_CASE_DIR/restart_data/$name"
    destination="$CASE_DIR/restart_data/$name"
    [[ -s "$source" ]] || {
        echo "ERROR: required t=$START_TIME checkpoint file is missing: $source" >&2
        exit 5
    }
    cp --reflink=auto --preserve=timestamps "$source" "$destination"
    [[ -s "$destination" && "$(stat -c %s "$source")" == "$(stat -c %s "$destination")" ]] || {
        echo "ERROR: restart copy verification failed: $name" >&2
        exit 5
    }
done

job="$({
    cd "$CASE_DIR"
    sbatch --parsable --ntasks=32 --mem=96G --time=12:00:00 \
        --constraint='intel&x86_64_v4' --job-name=mfc-a40-iles-t3t6 \
        --output="$CASE_DIR/slurm-%j.out" --error="$CASE_DIR/slurm-%j.err" \
        --export="ALL,CASE_DIR=$CASE_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,START_TIME=$START_TIME,STOP_TIME=$STOP_TIME,SAVE_DT=$SAVE_DT,DT_FACTOR=$DT_FACTOR,FPS=$FPS" \
        "$CASE_DIR/run_restart_stage.sbatch"
})"
job="${job%%;*}"
[[ "$job" =~ ^[0-9]+$ ]] || {
    echo "ERROR: invalid Slurm job id: $job" >&2
    exit 6
}

{
    printf 'JOB=%q\n' "$job"
    printf 'CASE_DIR=%q\n' "$CASE_DIR"
    printf 'START_STEP=%q\n' "$start_step"
    printf 'STOP_STEP=%q\n' "$stop_step"
    printf 'SAVE_STEP=%q\n' "$save_step"
    printf 'DT=%q\n' "$dt_value"
    printf 'ILES_REF=%q\n' "$ILES_REF"
} >"$CASE_DIR/submission.env"

echo "NEXT_STAGE_JOB=$job"
echo "CASE_DIR=$CASE_DIR"
echo "EXPECTED_FINAL_STEP=$stop_step"
echo "WATCH=squeue -j $job"
echo "STATUS=sacct -j $job -X --format=JobID,State,Elapsed,ExitCode"
