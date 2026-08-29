#!/usr/bin/env bash
set -Eeuo pipefail
trap 'rc=$?; trap - ERR; echo "ERROR: high-viscosity submitter stopped at line $LINENO (exit $rc)." >&2; exit "$rc"' ERR

PROJECT_ROOT=${PROJECT_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}
REPO_ROOT=${REPO_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification}
MFC_ILES_ROOT=${MFC_ILES_ROOT:-$REPO_ROOT/third_party/MFC-0c9a1d43-iles-portable-v3}
SOURCE_CASE=${SOURCE_CASE:-$PROJECT_ROOT/runs/mfc_iles_a40_fresh_hll_production/f270_t000_t0600_w5unmapped_hll_dt1_20260827-215512}
RUN_ROOT=${RUN_ROOT:-$PROJECT_ROOT/runs/mfc_iles_a40_tim_high_viscosity}
MAIL_USER=${MAIL_USER:-roohie@umass.edu}
RE_CHORD=${RE_CHORD:-10000}
MPI_RANKS=${MPI_RANKS:-32}
CONSTRAINT=${CONSTRAINT:-intel&x86_64_v4}
SLURM_USER=${SLURM_USER:-${USER:-$(id -un)}}
MAKER=$REPO_ROOT/mfc_iles_a40/tim_high_viscosity_control/make_high_viscosity_case.py
RUNNER=$REPO_ROOT/mfc_iles_a40/tim_high_viscosity_control/run_high_viscosity_initial.sbatch

CASE_SHA=f189828883d7d0c1ccc523868e1171ccd63c11af8cc4ce027eaf3003ee49236d
STL_SHA=65ea8cb922a7c092df652f630cc16904fc4920c0559ad7eb8664918ea7d6f210

[[ "$RE_CHORD" == 10000 ]] || { echo "ERROR: this pinned pilot requires Re_c=10000." >&2; exit 2; }
[[ "$MPI_RANKS" -eq 32 ]] || { echo "ERROR: this pilot retains the validated 32-rank layout." >&2; exit 2; }
[[ -x "$MAKER" && -x "$RUNNER" && -x "$MFC_ILES_ROOT/mfc.sh" ]] || {
    echo "ERROR: missing workflow or MFC executable" >&2
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
if squeue -h -u "$SLURM_USER" -o '%j' 2>/dev/null | grep -Eq '^mfc-tim-re1e4-(f180|f270)$'; then
    echo "ERROR: Tim high-viscosity pilot is already active" >&2
    exit 3
fi

mkdir -p "$RUN_ROOT"
available=$(df -PB1 "$RUN_ROOT" | awk 'NR==2 {print $4}')
required=70000000000
if [[ "$available" =~ ^[0-9]+$ ]] && ((available < required)); then
    echo "ERROR: less than 70 GB free for the two-grid pilot" >&2
    exit 4
fi
STAMP=$(date +%Y%m%d-%H%M%S)
PILOT_DIR=$RUN_ROOT/re1e4_t000_t060_$STAMP
mkdir -p "$PILOT_DIR"

grids=(f180 f270)
stop_steps=(21600 32400)
save_steps=(180 270)
field_bytes=(142560000 320760000)
memories=(64G 96G)
walltimes=(12:00:00 24:00:00)
jobs=()

for i in "${!grids[@]}"; do
    grid=${grids[$i]}
    case_dir=$PILOT_DIR/$grid
    mkdir -p "$case_dir/restart_data"
    cp -a "$SOURCE_CASE/Diamond_Airfoil_2D_MFC.stl" "$case_dir/"
    "$MAKER" --source "$SOURCE_CASE/case.py" --output "$case_dir/case.py" --re-chord "$RE_CHORD"
    python3 "$case_dir/case.py" --mode initial --grid "$grid" --start-time 0 \
        --final-time 6 --save-dt 0.05 --dt-factor 1 --format binary >"$case_dir/preflight-case.json"
    python3 - "$case_dir/preflight-case.json" "${stop_steps[$i]}" "${save_steps[$i]}" "$RE_CHORD" <<'PY'
import json, math, sys
c=json.load(open(sys.argv[1], encoding="utf-8"))
assert c["t_step_start"] == 0 and c["t_step_stop"] == int(sys.argv[2])
assert c["t_step_save"] == int(sys.argv[3]) and c["num_patches"] == 1
assert c["riemann_solver"] == 1 and c["weno_order"] == 5 and c["mapped_weno"] == "F"
assert c["viscous"] == "T" and c["ib_neighborhood_radius"] == 4
assert math.isclose(3.0*c["fluid_pp(1)%Re(1)"], float(sys.argv[4]), rel_tol=1e-12)
PY
    job=$(sbatch --parsable --ntasks="$MPI_RANKS" --mem="${memories[$i]}" \
        --time="${walltimes[$i]}" --constraint="$CONSTRAINT" \
        --job-name="mfc-tim-re1e4-$grid" --mail-user="$MAIL_USER" --mail-type=BEGIN,END,FAIL \
        --output="$case_dir/slurm-%j.out" --error="$case_dir/slurm-%j.err" \
        --export="ALL,CASE_DIR=$case_dir,MFC_ILES_ROOT=$MFC_ILES_ROOT,GRID=$grid,RE_CHORD=$RE_CHORD,STOP_TIME=6,SAVE_DT=0.05,EXPECTED_STOP_STEP=${stop_steps[$i]},EXPECTED_SAVE_STEP=${save_steps[$i]},EXPECTED_LUSTRE_BYTES=${field_bytes[$i]}" \
        "$RUNNER")
    job=${job%%;*}
    jobs+=("$job")
    printf 'GRID=%s\nJOB=%s\nCASE_DIR=%s\n' "$grid" "$job" "$case_dir" >"$case_dir/submission.env"
done

printf 'status=SUBMITTED\nRe_c=%s\nMAIL_USER=%s\nF180_JOB=%s\nF270_JOB=%s\n' \
    "$RE_CHORD" "$MAIL_USER" "${jobs[0]}" "${jobs[1]}" >"$PILOT_DIR/submission.env"
echo "TIM_HIGH_VISCOSITY_PREFLIGHT=PASS"
echo "ESTIMATED_CHECKPOINT_STORAGE_GB=57"
echo "TIM_HIGH_VISCOSITY_SUBMITTED=PASS"
echo "PILOT_DIR=$PILOT_DIR"
echo "F180_JOB=${jobs[0]}"
echo "F270_JOB=${jobs[1]}"
echo "MAIL_USER=$MAIL_USER"
echo "WATCH=squeue -j ${jobs[0]},${jobs[1]}"
