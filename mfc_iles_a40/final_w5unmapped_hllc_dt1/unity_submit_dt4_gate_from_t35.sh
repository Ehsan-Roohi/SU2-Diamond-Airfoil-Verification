#!/usr/bin/env bash
set -Eeuo pipefail

: "${ILES_REF:?Set ILES_REF to the pinned GitHub commit containing this script.}"

REPOSITORY=Ehsan-Roohi/SU2-Diamond-Airfoil-Verification
CASE_SUBDIR=mfc_iles_a40/final_w5unmapped_hllc_dt1
RAW_BASE="https://raw.githubusercontent.com/$REPOSITORY/$ILES_REF/$CASE_SUBDIR"
DATA_ROOT="${DATA_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}"
SOURCE_CASE_DIR="${SOURCE_CASE_DIR:-$DATA_ROOT/runs/mfc_iles_a40_continuation/f270_t30_t60_w5unmapped_hllc_dt1_20260823-152240}"
RUN_ROOT="${RUN_ROOT:-$DATA_ROOT/runs/mfc_iles_a40_dt4_recovery}"
MFC_ILES_ROOT="${MFC_ILES_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
START_TIME="${START_TIME:-3.5}"
STOP_TIME="${STOP_TIME:-3.7}"
SAVE_DT="${SAVE_DT:-0.025}"
DT_FACTOR=4
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
[[ -s "$SOURCE_CASE_DIR/preflight-restart.json" ]] || {
    echo "ERROR: missing source clock provenance: $SOURCE_CASE_DIR/preflight-restart.json" >&2
    exit 3
}
[[ -s "$SOURCE_CASE_DIR/RUN_FAILED_BUT_FIELDS_RECOVERED.txt" ]] || {
    echo "ERROR: expected recovered-fields marker is missing." >&2
    exit 3
}

read -r source_step source_dt < <(
    python3 - "$SOURCE_CASE_DIR" "$START_TIME" <<'PY'
import json
import math
import pathlib
import sys

case_dir = pathlib.Path(sys.argv[1])
start_time = float(sys.argv[2])
source_case = json.loads((case_dir / "preflight-restart.json").read_text())
source_dt = float(source_case["dt"])
source_step = round(start_time / source_dt)
assert math.isclose(source_step * source_dt, start_time, rel_tol=0.0, abs_tol=1.0e-12)

audits = list((case_dir / "movie_products").glob("*field-audit.json"))
assert audits, "field audit is missing"
audit = json.loads(max(audits, key=lambda p: p.stat().st_mtime).read_text())
assert audit.get("pass") is True, "source field audit did not pass"
rows = audit.get("audit_steps", [])
assert rows and int(rows[-1]["step"]) == source_step, "audit does not end at requested rollback checkpoint"
assert rows[-1].get("finite") is True and rows[-1].get("pass") is True
assert float(rows[-1]["rho_min"]) > 0.0 and float(rows[-1]["pres_min"]) > 0.0
print(source_step, repr(source_dt))
PY
)
grep -qx "latest_step=$source_step" \
    "$SOURCE_CASE_DIR/RUN_FAILED_BUT_FIELDS_RECOVERED.txt" || {
    echo "ERROR: recovery marker does not identify checkpoint $source_step." >&2
    exit 3
}

timestamp="$(date +%Y%m%d-%H%M%S)"
CASE_DIR="$RUN_ROOT/f270_t${START_TIME//./}_t${STOP_TIME//./}_dt4_w5unmapped_hllc_$timestamp"
[[ ! -e "$CASE_DIR" ]] || {
    echo "ERROR: destination already exists: $CASE_DIR" >&2
    exit 4
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
    awk '$2 == "case.py" || $2 == "Diamond_Airfoil_2D_MFC.stl" || \
         $2 == "make_recovery_movies.py" || $2 == "run_restart_stage.sbatch"' \
        SHA256SUMS.txt >RECOVERY_CORE_SHA256SUMS.txt
    [[ "$(wc -l <RECOVERY_CORE_SHA256SUMS.txt)" -eq 4 ]] || {
        echo "ERROR: incomplete recovery checksum list." >&2
        exit 5
    }
    sha256sum -c RECOVERY_CORE_SHA256SUMS.txt
)

preflight="$CASE_DIR/preflight-dt4-restart.json"
python3 "$CASE_DIR/case.py" --mode restart --grid f270 \
    --start-time "$START_TIME" --final-time "$STOP_TIME" \
    --save-dt "$SAVE_DT" --dt-factor "$DT_FACTOR" --format binary \
    >"$preflight"

read -r target_start target_stop target_save target_dt < <(
    python3 - "$preflight" <<'PY'
import json
import sys

case = json.load(open(sys.argv[1]))
assert case["mapped_weno"] == "F"
assert case["weno_order"] == 5
assert case["riemann_solver"] == 2 and case["wave_speeds"] == 1
assert case["ib_neighborhood_radius"] == 4
assert case["old_ic"] == "T" and case["old_grid"] == "T"
print(case["t_step_start"], case["t_step_stop"], case["t_step_save"], repr(case["dt"]))
PY
)
[[ "$target_start" -eq $((4 * source_step)) ]] || {
    echo "ERROR: unsafe restart reindex: source=$source_step target=$target_start" >&2
    exit 6
}

for stem in lustre ib_state; do
    source="$SOURCE_CASE_DIR/restart_data/${stem}_${source_step}.dat"
    destination="$CASE_DIR/restart_data/${stem}_${target_start}.dat"
    [[ -s "$source" ]] || {
        echo "ERROR: source checkpoint is missing: $source" >&2
        exit 6
    }
    cp --reflink=auto --preserve=timestamps "$source" "$destination"
    [[ -s "$destination" && "$(stat -c %s "$source")" == "$(stat -c %s "$destination")" ]] || {
        echo "ERROR: checkpoint reindex copy failed: $stem" >&2
        exit 6
    }
done
for name in lustre_x_cb.dat lustre_y_cb.dat; do
    source="$SOURCE_CASE_DIR/restart_data/$name"
    destination="$CASE_DIR/restart_data/$name"
    [[ -s "$source" ]] || { echo "ERROR: missing $source" >&2; exit 6; }
    cp --reflink=auto --preserve=timestamps "$source" "$destination"
    [[ -s "$destination" ]] || { echo "ERROR: failed to copy $name" >&2; exit 6; }
done

job="$({
    cd "$CASE_DIR"
    sbatch --parsable --ntasks=32 --mem=96G --time=06:00:00 \
        --constraint='intel&x86_64_v4' --job-name=mfc-a40-dt4-gate37 \
        --output="$CASE_DIR/slurm-%j.out" --error="$CASE_DIR/slurm-%j.err" \
        --export="ALL,CASE_DIR=$CASE_DIR,MFC_ILES_ROOT=$MFC_ILES_ROOT,START_TIME=$START_TIME,STOP_TIME=$STOP_TIME,SAVE_DT=$SAVE_DT,DT_FACTOR=$DT_FACTOR,FPS=$FPS" \
        "$CASE_DIR/run_restart_stage.sbatch"
})"
job="${job%%;*}"
[[ "$job" =~ ^[0-9]+$ ]] || {
    echo "ERROR: invalid Slurm job id: $job" >&2
    exit 7
}

{
    printf 'JOB=%q\n' "$job"
    printf 'CASE_DIR=%q\n' "$CASE_DIR"
    printf 'SOURCE_CASE_DIR=%q\n' "$SOURCE_CASE_DIR"
    printf 'SOURCE_STEP=%q\n' "$source_step"
    printf 'SOURCE_DT=%q\n' "$source_dt"
    printf 'TARGET_START_STEP=%q\n' "$target_start"
    printf 'TARGET_STOP_STEP=%q\n' "$target_stop"
    printf 'TARGET_SAVE_STEP=%q\n' "$target_save"
    printf 'TARGET_DT=%q\n' "$target_dt"
    printf 'ILES_REF=%q\n' "$ILES_REF"
} >"$CASE_DIR/submission.env"

echo "DT4_GATE_JOB=$job"
echo "CASE_DIR=$CASE_DIR"
echo "ROLLBACK_SOURCE_STEP=$source_step"
echo "REINDEXED_START_STEP=$target_start"
echo "EXPECTED_FINAL_STEP=$target_stop"
echo "WATCH=squeue -j $job"
