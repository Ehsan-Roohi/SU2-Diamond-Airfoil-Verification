#!/usr/bin/env bash
set -Eeuo pipefail
trap 'rc=$?; trap - ERR; echo "ERROR: Stage-4 submitter stopped at line $LINENO (exit $rc)." >&2; exit "$rc"' ERR

PROJECT_ROOT=${PROJECT_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}
REPO_ROOT=${REPO_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification}
MFC_ILES_ROOT=${MFC_ILES_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}
SOURCE_CASE=${SOURCE_CASE:-$PROJECT_ROOT/runs/mfc_iles_a40_fresh_hll_production/f270_t000_t0600_w5unmapped_hll_dt1_20260827-215512}
RUN_ROOT=${RUN_ROOT:-$PROJECT_ROOT/runs/mfc_iles_a40_tim_viscosity_ladder}
MAIL_USER=${MAIL_USER:-roohie@umass.edu}
MPI_RANKS=${MPI_RANKS:-32}
CONSTRAINT=${CONSTRAINT:-intel&x86_64_v4}
WORKFLOW_REV=${WORKFLOW_REV:-UNKNOWN}
SLURM_USER=${SLURM_USER:-${USER:-$(id -un)}}

BASE=mfc_iles_a40/tim_high_viscosity_control
MAKER=$REPO_ROOT/$BASE/make_high_viscosity_case.py
RUNNER=$REPO_ROOT/$BASE/run_high_viscosity_initial.sbatch

CASE_SHA=f189828883d7d0c1ccc523868e1171ccd63c11af8cc4ce027eaf3003ee49236d
STL_SHA=65ea8cb922a7c092df652f630cc16904fc4920c0559ad7eb8664918ea7d6f210
STOP_TIME=6
EXPECTED_STOP_STEP=21600
SAVE_DT=0.1
EXPECTED_SAVE_STEP=360
EXPECTED_LUSTRE_BYTES=142560000

[[ "$MPI_RANKS" -eq 32 ]] || { echo "ERROR: validated layout requires 32 MPI ranks." >&2; exit 2; }
[[ "$WORKFLOW_REV" =~ ^[0-9a-f]{40}$ ]] || { echo "ERROR: pass the pinned 40-character WORKFLOW_REV." >&2; exit 2; }
[[ -x "$MAKER" && -x "$RUNNER" && -x "$MFC_ILES_ROOT/mfc.sh" ]] || {
    echo "ERROR: missing pinned workflow or MFC executable" >&2
    exit 2
}
[[ -f "$SOURCE_CASE/RUN_OK_INITIAL.txt" ]] || { echo "ERROR: verified HLL source marker is missing" >&2; exit 2; }
[[ "$(sha256sum "$SOURCE_CASE/case.py" | awk '{print $1}')" == "$CASE_SHA" ]] || {
    echo "ERROR: HLL source case hash mismatch" >&2
    exit 2
}
[[ "$(sha256sum "$SOURCE_CASE/Diamond_Airfoil_2D_MFC.stl" | awk '{print $1}')" == "$STL_SHA" ]] || {
    echo "ERROR: source STL hash mismatch" >&2
    exit 2
}
if squeue -h -u "$SLURM_USER" -o '%j' 2>/dev/null | grep -Eq '^mfc-tim-r(5e4|1e5)-f180$'; then
    echo "ERROR: a Stage-4 viscosity-ladder job is already active" >&2
    exit 3
fi

mkdir -p "$RUN_ROOT"
available=$(df -PB1 "$RUN_ROOT" | awk 'NR==2 {print $4}')
required=55000000000
if [[ "$available" =~ ^[0-9]+$ ]] && ((available < required)); then
    echo "ERROR: less than 55 GB free; Stage 4 is not submitted." >&2
    echo "AVAILABLE_BYTES=$available" >&2
    exit 4
fi

STAMP=$(date +%Y%m%d-%H%M%S)
LADDER_DIR=$RUN_ROOT/f180_re5e4_re1e5_t000_t060_$STAMP
mkdir -p "$LADDER_DIR"

re_values=(50000 100000)
re_tags=(5e4 1e5)
jobs=()

for i in "${!re_values[@]}"; do
    re=${re_values[$i]}
    tag=${re_tags[$i]}
    case_dir=$LADDER_DIR/re$tag
    mkdir -p "$case_dir/restart_data"
    cp -a "$SOURCE_CASE/Diamond_Airfoil_2D_MFC.stl" "$case_dir/"
    "$MAKER" --source "$SOURCE_CASE/case.py" --output "$case_dir/case.py" --re-chord "$re"
    python3 "$case_dir/case.py" --mode initial --grid f180 --start-time 0 \
        --final-time "$STOP_TIME" --save-dt "$SAVE_DT" --dt-factor 1 --format binary \
        >"$case_dir/preflight-case.json"
    python3 - "$case_dir/preflight-case.json" "$re" <<'PY'
import json, math, sys
c=json.load(open(sys.argv[1], encoding="utf-8"))
assert c["m"] == 1979 and c["n"] == 1799
assert c["t_step_start"] == 0 and c["t_step_stop"] == 21600
assert c["t_step_save"] == 360 and c["num_patches"] == 1
assert c["riemann_solver"] == 1 and c["weno_order"] == 5 and c["mapped_weno"] == "F"
assert c["viscous"] == "T" and c["ib_neighborhood_radius"] == 4
assert math.isclose(3.0*c["fluid_pp(1)%Re(1)"], float(sys.argv[2]), rel_tol=1e-12)
PY
    job=$(sbatch --parsable --nice=5000 --ntasks="$MPI_RANKS" --mem=24G --time=08:00:00 \
        --constraint="$CONSTRAINT" --job-name="mfc-tim-r$tag-f180" \
        --mail-user="$MAIL_USER" --mail-type=BEGIN,END,FAIL \
        --output="$case_dir/slurm-%j.out" --error="$case_dir/slurm-%j.err" \
        --export="ALL,CASE_DIR=$case_dir,MFC_ILES_ROOT=$MFC_ILES_ROOT,GRID=f180,RE_CHORD=$re,STOP_TIME=$STOP_TIME,SAVE_DT=$SAVE_DT,EXPECTED_STOP_STEP=$EXPECTED_STOP_STEP,EXPECTED_SAVE_STEP=$EXPECTED_SAVE_STEP,EXPECTED_LUSTRE_BYTES=$EXPECTED_LUSTRE_BYTES" \
        "$RUNNER")
    job=${job%%;*}
    jobs+=("$job")
    printf 'status=SUBMITTED\nRe_c=%s\ngrid=f180\njob=%s\nworkflow_rev=%s\ncase_dir=%s\n' \
        "$re" "$job" "$WORKFLOW_REV" "$case_dir" >"$case_dir/submission.env"
done

printf 'status=SUBMITTED\nworkflow_rev=%s\nmail_user=%s\nre5e4_job=%s\nre1e5_job=%s\n' \
    "$WORKFLOW_REV" "$MAIL_USER" "${jobs[0]}" "${jobs[1]}" >"$LADDER_DIR/submission.env"

echo "VISCOSITY_LADDER_STAGE4_PREFLIGHT=PASS"
echo "VISCOSITY_LADDER_STAGE4_SUBMITTED=PASS"
echo "LADDER_DIR=$LADDER_DIR"
echo "RE5E4_JOB=${jobs[0]}"
echo "RE1E5_JOB=${jobs[1]}"
echo "GRID=f180"
echo "TIME_RANGE=0:6"
echo "SAVE_DT=0.1"
echo "EXPECTED_SNAPSHOTS_PER_CASE=61"
echo "ESTIMATED_TOTAL_FIELD_STORAGE_GB=17.4"
echo "MAIL_USER=$MAIL_USER"
echo "WATCH=squeue -j ${jobs[0]},${jobs[1]}"
