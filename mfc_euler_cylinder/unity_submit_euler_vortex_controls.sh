#!/usr/bin/env bash
set -Eeuo pipefail

# Submit the two controls required to distinguish persistent Euler wake
# structure from grid/time-step and immersed-boundary artifacts.  The completed
# f90/CFL=0.20 trajectory is the baseline and is intentionally not repeated.

REPOSITORY="Ehsan-Roohi/SU2-Diamond-Airfoil-Verification"
SOURCE_REF="agent/mfc-euler-cylinder-validation"
LAUNCHER_URL="https://raw.githubusercontent.com/$REPOSITORY/$SOURCE_REF/mfc_euler_cylinder/unity_submit_euler_cylinder.sh"

DEFAULT_ROOT=/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification
DEFAULT_MFC_SOURCE_ROOT="$DEFAULT_ROOT/third_party/MFC-0c9a1d43"
DEFAULT_SCRATCH_ROOT=/scratch4/workspace/roohie_umass_edu-mfc-a40-cv

ROOT="${ROOT:-$DEFAULT_ROOT}"
MFC_SOURCE_ROOT="${MFC_SOURCE_ROOT:-$DEFAULT_MFC_SOURCE_ROOT}"
CONTROL_ROOT="${CONTROL_ROOT:-$DEFAULT_SCRATCH_ROOT/mfc_euler_cylinder_vortex_controls}"
MFC_CYL_PARENT="${MFC_CYL_PARENT:-$DEFAULT_SCRATCH_ROOT/mfc_euler_cylinder_control_builds}"
MACH="${MACH:-2.7}"
FINAL_TIME="${FINAL_TIME:-8}"
SAVE_DT="${SAVE_DT:-0.1}"
AFTER_JOB="${AFTER_JOB:-none}"
RUN_SET_ID="${RUN_SET_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"

for path in "$ROOT" "$MFC_SOURCE_ROOT" "$CONTROL_ROOT" "$MFC_CYL_PARENT"; do
    if [[ "$path" != /* || "$path" == / ]]; then
        echo "ERROR: all configured roots must be non-root absolute paths: $path" >&2
        exit 2
    fi
done
[[ -x "$MFC_SOURCE_ROOT/mfc.sh" ]] || {
    echo "ERROR: pinned MFC source is missing: $MFC_SOURCE_ROOT" >&2
    exit 2
}

SET_ROOT="$CONTROL_ROOT/$RUN_SET_ID"
MANIFEST="$SET_ROOT/CONTROL_MANIFEST.env"
if [[ -e "$MANIFEST" ]]; then
    echo "ERROR: RUN_SET_ID already has a manifest: $MANIFEST" >&2
    exit 2
fi
mkdir -p "$SET_ROOT"
LAUNCHER="$SET_ROOT/unity_submit_euler_cylinder.sh"
curl -fL --retry 3 "$LAUNCHER_URL" -o "$LAUNCHER"
chmod u+x "$LAUNCHER"

submit_control() {
    local label="$1"
    local grid="$2"
    local cfl="$3"
    local data_root="$SET_ROOT/$label/data"
    local build_root="$MFC_CYL_PARENT/$label"
    local log="$SET_ROOT/$label-submit.log"
    local output

    output="$(env \
        ROOT="$ROOT" \
        DATA_ROOT="$data_root" \
        MFC_SOURCE_ROOT="$MFC_SOURCE_ROOT" \
        MFC_CYL_ROOT="$build_root" \
        MACH="$MACH" \
        REYNOLDS=0 \
        GRID="$grid" \
        CFL_COEFFICIENT="$cfl" \
        FINAL_TIME="$FINAL_TIME" \
        SAVE_DT="$SAVE_DT" \
        AFTER_JOB="$AFTER_JOB" \
        bash "$LAUNCHER")"
    printf '%s\n' "$output" | tee "$log" >&2

    local smoke_job
    local prod_job
    local run_base
    local case_dir
    smoke_job="$(printf '%s\n' "$output" | awk -F= '$1 == "SMOKE_JOB" {print $2}')"
    prod_job="$(printf '%s\n' "$output" | awk -F= '$1 == "PROD_JOB" {print $2}')"
    run_base="$(printf '%s\n' "$output" | awk -F= '$1 == "RUN_BASE" {sub($1 "=", ""); print}')"
    case_dir="$(printf '%s\n' "$output" | awk -F= '$1 == "CASE_DIR" {sub($1 "=", ""); print}')"
    [[ "$smoke_job" =~ ^[0-9]+$ && "$prod_job" =~ ^[0-9]+$ ]] || {
        echo "ERROR: could not recover Slurm job IDs for $label" >&2
        exit 4
    }
    printf '%s\t%s\t%s\t%s\n' "$smoke_job" "$prod_job" "$run_base" "$case_dir"
}

# B: spatial-resolution control, compared with the existing f90/CFL=0.20 run.
IFS=$'\t' read -r GRID_SMOKE_JOB GRID_PROD_JOB GRID_RUN_BASE GRID_CASE_DIR < <(
    submit_control grid_f180_cfl0p20 f180 0.20
)

# C: time-step control at fixed f90 mesh.
IFS=$'\t' read -r CFL_SMOKE_JOB CFL_PROD_JOB CFL_RUN_BASE CFL_CASE_DIR < <(
    submit_control timestep_f90_cfl0p10 f90 0.10
)

{
    printf 'STATUS=%q\n' SUBMITTED
    printf 'CREATED_UTC=%q\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'SOURCE_REF=%q\n' "$SOURCE_REF"
    printf 'MACH=%q\n' "$MACH"
    printf 'FINAL_TIME=%q\n' "$FINAL_TIME"
    printf 'SAVE_DT=%q\n' "$SAVE_DT"
    printf 'BASELINE=%q\n' 'existing_f90_cfl0p20'
    printf 'GRID_CONTROL=%q\n' 'f180_cfl0p20'
    printf 'GRID_SMOKE_JOB=%q\n' "$GRID_SMOKE_JOB"
    printf 'GRID_PROD_JOB=%q\n' "$GRID_PROD_JOB"
    printf 'GRID_RUN_BASE=%q\n' "$GRID_RUN_BASE"
    printf 'GRID_CASE_DIR=%q\n' "$GRID_CASE_DIR"
    printf 'TIMESTEP_CONTROL=%q\n' 'f90_cfl0p10'
    printf 'CFL_SMOKE_JOB=%q\n' "$CFL_SMOKE_JOB"
    printf 'CFL_PROD_JOB=%q\n' "$CFL_PROD_JOB"
    printf 'CFL_RUN_BASE=%q\n' "$CFL_RUN_BASE"
    printf 'CFL_CASE_DIR=%q\n' "$CFL_CASE_DIR"
    printf 'JOBS=%q\n' "$GRID_SMOKE_JOB,$GRID_PROD_JOB,$CFL_SMOKE_JOB,$CFL_PROD_JOB"
} >"$MANIFEST"

echo "CONTROL_SET_ROOT=$SET_ROOT"
echo "CONTROL_MANIFEST=$MANIFEST"
echo "GRID_CONTROL_PROD_JOB=$GRID_PROD_JOB"
echo "TIMESTEP_CONTROL_PROD_JOB=$CFL_PROD_JOB"
echo "NEXT: source '$MANIFEST'; squeue -j \"\$JOBS\""
