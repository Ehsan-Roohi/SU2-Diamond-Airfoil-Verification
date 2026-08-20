#!/usr/bin/env bash
set -euo pipefail

TARGET=${1:-both}
case "$TARGET" in
    fine|very-fine|both) ;;
    *) echo "usage: $0 [fine|very-fine|both]" >&2; exit 2 ;;
esac

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
MFC_ROOT=${MFC_ROOT:-"$PROJECT_ROOT/third_party/MFC-0c9a1d43"}
STAMP=$(date +%Y%m%d-%H%M%S)
RUN_BASE=${RUN_BASE:-"$PROJECT_ROOT/mfc_runs/fixed_ib_a40_${STAMP}"}
SLURM_SCRIPT="$SCRIPT_DIR/slurm/mfc_case.slurm"

[[ -x "$MFC_ROOT/mfc.sh" ]] || {
    echo "MFC executable not found: $MFC_ROOT/mfc.sh" >&2
    exit 3
}

python3 "$SCRIPT_DIR/validate_mfc_2d_stl.py" \
    "$SCRIPT_DIR/Diamond_Airfoil_2D_MFC.stl" --expected-edges 4

prepare_case() {
    local destination=$1
    mkdir -p "$destination"
    install -m 0644 "$SCRIPT_DIR/case.py" "$destination/case.py"
    install -m 0644 "$SCRIPT_DIR/Diamond_Airfoil_2D_MFC.stl" \
        "$destination/Diamond_Airfoil_2D_MFC.stl"
    install -m 0755 "$SCRIPT_DIR/validate_mfc_2d_stl.py" \
        "$destination/validate_mfc_2d_stl.py"
}

submit_case() {
    local case_dir=$1
    local job_name=$2
    local tasks=$3
    local memory=$4
    local walltime=$5
    local grid=$6
    local steps=$7
    local save_every=$8
    local dependency=${9:-}
    local dependency_args=()
    if [[ -n "$dependency" ]]; then
        dependency_args=(--dependency="afterok:$dependency")
    fi

    sbatch --parsable \
        --partition=cpu \
        --nodes=1 \
        --ntasks="$tasks" \
        --cpus-per-task=1 \
        --mem="$memory" \
        --time="$walltime" \
        --job-name="$job_name" \
        --output="$case_dir/slurm-%j.out" \
        --error="$case_dir/slurm-%j.err" \
        "${dependency_args[@]}" \
        --export="ALL,CASE_DIR=$case_dir,MFC_ROOT=$MFC_ROOT,ALPHA=40,GRID=$grid,STEPS=$steps,SAVE_EVERY=$save_every" \
        "$SLURM_SCRIPT"
}

mkdir -p "$RUN_BASE"
SMOKE_DIR="$RUN_BASE/smoke"
prepare_case "$SMOKE_DIR"
SMOKE_JOB=$(submit_case "$SMOKE_DIR" mfc-a40-smoke 4 16G 00:30:00 smoke 20 20)

FINE_JOB=
VERY_FINE_JOB=

if [[ "$TARGET" == fine || "$TARGET" == both ]]; then
    FINE_DIR="$RUN_BASE/fine_f180_t13p5"
    prepare_case "$FINE_DIR"
    FINE_JOB=$(submit_case "$FINE_DIR" mfc-a40-f180 16 64G 20:00:00 \
        fine 48600 1944 "$SMOKE_JOB")
fi

if [[ "$TARGET" == very-fine || "$TARGET" == both ]]; then
    VERY_FINE_DIR="$RUN_BASE/very_fine_f270_t13p5"
    prepare_case "$VERY_FINE_DIR"
    VERY_FINE_JOB=$(submit_case "$VERY_FINE_DIR" mfc-a40-f270 32 128G 36:00:00 \
        very-fine 72900 2916 "$SMOKE_JOB")
fi

SUBMISSION_ENV="$RUN_BASE/submission.env"
{
    printf 'RUN_BASE=%q\n' "$RUN_BASE"
    printf 'SMOKE_JOB=%q\n' "$SMOKE_JOB"
    printf 'FINE_JOB=%q\n' "$FINE_JOB"
    printf 'VERY_FINE_JOB=%q\n' "$VERY_FINE_JOB"
} > "$SUBMISSION_ENV"

echo "RUN_BASE=$RUN_BASE"
echo "SMOKE_JOB=$SMOKE_JOB"
[[ -n "$FINE_JOB" ]] && echo "FINE_JOB=$FINE_JOB"
[[ -n "$VERY_FINE_JOB" ]] && echo "VERY_FINE_JOB=$VERY_FINE_JOB"
echo "SUBMISSION_ENV=$SUBMISSION_ENV"
