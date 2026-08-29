#!/usr/bin/env bash
set -Eeuo pipefail
trap 'rc=$?; trap - ERR; echo "ERROR: t6→t36 submitter stopped at line $LINENO (exit $rc)." >&2; exit "$rc"' ERR

PROJECT_ROOT=${PROJECT_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}
REPO_ROOT=${REPO_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification}
MFC_ILES_ROOT=${MFC_ILES_ROOT:-$REPO_ROOT/third_party/MFC-0c9a1d43-iles-portable-v3}
SOURCE_CASE=${SOURCE_CASE:-$PROJECT_ROOT/runs/mfc_iles_a40_fresh_hll_production/f270_t000_t0600_w5unmapped_hll_dt1_20260827-215512}
RUN_ROOT=${RUN_ROOT:-$PROJECT_ROOT/runs/mfc_iles_a40_hll_long_baseline}
MAIL_USER=${MAIL_USER:-roohie@umass.edu}
MPI_RANKS=${MPI_RANKS:-32}
MEMORY=${MEMORY:-96G}
WALLTIME=${WALLTIME:-24:00:00}
CONSTRAINT=${CONSTRAINT:-intel&x86_64_v4}
SAVE_DT=${SAVE_DT:-0.05}
SLURM_USER=${SLURM_USER:-${USER:-$(id -un)}}
RUNNER=$REPO_ROOT/mfc_iles_a40/hll_long_baseline/run_hll_restart_stage.sbatch

CASE_SHA=f189828883d7d0c1ccc523868e1171ccd63c11af8cc4ce027eaf3003ee49236d
STL_SHA=65ea8cb922a7c092df652f630cc16904fc4920c0559ad7eb8664918ea7d6f210
EXPECTED_LUSTRE_BYTES=320760000
EXPECTED_IB_BYTES=160

[[ "$MPI_RANKS" -eq 32 ]] || { echo "ERROR: only the validated 32-rank layout is allowed by this launcher." >&2; exit 2; }
[[ "$SAVE_DT" == 0.05 ]] || { echo "ERROR: the article sampling interval must remain 0.05." >&2; exit 2; }
[[ -x "$RUNNER" ]] || { echo "ERROR: missing stage runner: $RUNNER" >&2; exit 2; }
[[ -x "$MFC_ILES_ROOT/mfc.sh" ]] || { echo "ERROR: missing pinned MFC checkout." >&2; exit 2; }
[[ -f "$SOURCE_CASE/RUN_OK_INITIAL.txt" ]] || { echo "ERROR: verified t=6 marker is missing." >&2; exit 2; }
[[ "$(sha256sum "$SOURCE_CASE/case.py" | awk '{print $1}')" == "$CASE_SHA" ]] || { echo "ERROR: source case.py hash mismatch." >&2; exit 2; }
[[ "$(sha256sum "$SOURCE_CASE/Diamond_Airfoil_2D_MFC.stl" | awk '{print $1}')" == "$STL_SHA" ]] || { echo "ERROR: source STL hash mismatch." >&2; exit 2; }
[[ "$(stat -c %s "$SOURCE_CASE/restart_data/lustre_32400.dat")" -eq "$EXPECTED_LUSTRE_BYTES" ]] || { echo "ERROR: invalid t=6 lustre checkpoint." >&2; exit 2; }
[[ "$(stat -c %s "$SOURCE_CASE/restart_data/ib_state_32400.dat")" -eq "$EXPECTED_IB_BYTES" ]] || { echo "ERROR: invalid t=6 ib_state checkpoint." >&2; exit 2; }
for grid_file in lustre_x_cb.dat lustre_y_cb.dat; do
    [[ -s "$SOURCE_CASE/restart_data/$grid_file" ]] || { echo "ERROR: missing $grid_file" >&2; exit 2; }
done

if squeue -h -u "$SLURM_USER" -o '%j' 2>/dev/null | grep -Eq '^mfc-hll-(START-06-11|11-16|READY-T21|21-26|26-31|READY-T36)$'; then
    echo "ERROR: an HLL t6→t36 chain is already active; refusing duplicate submission." >&2
    exit 3
fi

mkdir -p "$RUN_ROOT"
STAMP=$(date +%Y%m%d-%H%M%S)
CHAIN_DIR=$RUN_ROOT/f270_t060_t360_hll_dt1_$STAMP
mkdir -p "$CHAIN_DIR"

starts=(6 11 16 21 26 31)
stops=(11 16 21 26 31 36)
start_steps=(32400 59400 86400 113400 140400 167400)
stop_steps=(59400 86400 113400 140400 167400 194400)
labels=(t06_t11 t11_t16 t16_t21 t21_t26 t26_t31 t31_t36)
job_names=(mfc-hll-START-06-11 mfc-hll-11-16 mfc-hll-READY-T21 mfc-hll-21-26 mfc-hll-26-31 mfc-hll-READY-T36)
mail_types=(BEGIN,FAIL FAIL END,FAIL FAIL FAIL END,FAIL)
stage_dirs=()

source_dir=$SOURCE_CASE
for i in "${!starts[@]}"; do
    stage_dir=$CHAIN_DIR/${labels[$i]}
    mkdir -p "$stage_dir/restart_data"
    cp -a "$SOURCE_CASE/case.py" "$SOURCE_CASE/Diamond_Airfoil_2D_MFC.stl" "$stage_dir/"
    python3 "$stage_dir/case.py" --mode restart --grid f270 \
        --start-time "${starts[$i]}" --final-time "${stops[$i]}" \
        --save-dt "$SAVE_DT" --dt-factor 1 --format binary >"$stage_dir/preflight-case.json"
    python3 - "$stage_dir/preflight-case.json" "${start_steps[$i]}" "${stop_steps[$i]}" <<'PY'
import json
import sys
c = json.load(open(sys.argv[1], encoding="utf-8"))
assert c["t_step_start"] == int(sys.argv[2])
assert c["t_step_stop"] == int(sys.argv[3])
assert c["t_step_save"] == 270
assert c["num_patches"] == 0 and c["old_ic"] == "T" and c["old_grid"] == "T"
assert c["riemann_solver"] == 1 and c["weno_order"] == 5 and c["mapped_weno"] == "F"
assert c["viscous"] == "T" and c["ib_neighborhood_radius"] == 4
PY
    printf 'STAGE=%s\nSOURCE_DIR=%s\nCASE_DIR=%s\nSTART_TIME=%s\nSTOP_TIME=%s\nSTART_STEP=%s\nSTOP_STEP=%s\n' \
        "${labels[$i]}" "$source_dir" "$stage_dir" "${starts[$i]}" "${stops[$i]}" \
        "${start_steps[$i]}" "${stop_steps[$i]}" >"$stage_dir/stage.env"
    stage_dirs+=("$stage_dir")
    source_dir=$stage_dir
done

available=$(df -PB1 "$RUN_ROOT" | awk 'NR==2 {print $4}')
required=220000000000
if [[ "$available" =~ ^[0-9]+$ ]] && ((available < required)); then
    echo "ERROR: less than 220 GB free on the target filesystem." >&2
    exit 4
fi
echo "HLL_T36_PREFLIGHT=PASS"
echo "ESTIMATED_CHECKPOINT_STORAGE_GB=195"

jobs=()
previous_job=
source_dir=$SOURCE_CASE
for i in "${!starts[@]}"; do
    stage_dir=${stage_dirs[$i]}
    dependency=()
    if [[ -n "$previous_job" ]]; then
        dependency=(--dependency="afterok:$previous_job")
    fi
    job=$(sbatch --parsable \
        --ntasks="$MPI_RANKS" \
        --mem="$MEMORY" \
        --time="$WALLTIME" \
        --constraint="$CONSTRAINT" \
        --job-name="${job_names[$i]}" \
        --mail-user="$MAIL_USER" \
        --mail-type="${mail_types[$i]}" \
        --output="$stage_dir/slurm-%j.out" \
        --error="$stage_dir/slurm-%j.err" \
        "${dependency[@]}" \
        --export="ALL,STAGE_LABEL=${labels[$i]},SOURCE_DIR=$source_dir,CASE_DIR=$stage_dir,MFC_ILES_ROOT=$MFC_ILES_ROOT,START_TIME=${starts[$i]},STOP_TIME=${stops[$i]},SAVE_DT=$SAVE_DT,EXPECTED_START_STEP=${start_steps[$i]},EXPECTED_STOP_STEP=${stop_steps[$i]},EXPECTED_SAVE_STEP=270,EXPECTED_LUSTRE_BYTES=$EXPECTED_LUSTRE_BYTES,EXPECTED_IB_BYTES=$EXPECTED_IB_BYTES" \
        "$RUNNER")
    job=${job%%;*}
    jobs+=("$job")
    printf 'JOB_%s=%s\n' "${labels[$i]}" "$job" >>"$CHAIN_DIR/submission.env"
    previous_job=$job
    source_dir=$stage_dir
done

printf 'CHAIN_DIR=%s\nMAIL_USER=%s\nMPI_RANKS=%s\nMILESTONE_T21_JOB=%s\nFINAL_T36_JOB=%s\n' \
    "$CHAIN_DIR" "$MAIL_USER" "$MPI_RANKS" "${jobs[2]}" "${jobs[5]}" >>"$CHAIN_DIR/submission.env"

echo "HLL_T36_CHAIN_SUBMITTED=PASS"
echo "CHAIN_DIR=$CHAIN_DIR"
echo "START_JOB=${jobs[0]}"
echo "MILESTONE_T21_JOB=${jobs[2]}"
echo "FINAL_T36_JOB=${jobs[5]}"
echo "MAIL_USER=$MAIL_USER"
echo "WATCH=squeue -j $(IFS=,; echo "${jobs[*]}")"
