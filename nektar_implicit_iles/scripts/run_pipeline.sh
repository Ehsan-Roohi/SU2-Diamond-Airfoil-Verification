#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 PROFILE ALPHA RUN_DIR" >&2
    exit 2
fi

PROFILE_NAME=$1
ALPHA_DEG=$2
RUN_DIR=$3
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BUNDLE_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
PROFILE_FILE="$BUNDLE_ROOT/profiles/${PROFILE_NAME}.env"

if [[ ! -f "$PROFILE_FILE" ]]; then
    echo "unknown profile: $PROFILE_NAME" >&2
    exit 2
fi
if [[ "$ALPHA_DEG" != "0" && "$ALPHA_DEG" != "4" && "$ALPHA_DEG" != "8" ]]; then
    echo "alpha must be 0, 4, or 8 degrees" >&2
    exit 2
fi

set -a
source "$PROFILE_FILE"
set +a

mkdir -p "$RUN_DIR"
RUN_DIR=$(cd "$RUN_DIR" && pwd)
cd "$RUN_DIR"
cp "$PROFILE_FILE" profile_used.env
printf 'profile=%s\nalpha_deg=%s\nbundle=%s\n' "$PROFILE_NAME" "$ALPHA_DEG" "$BUNDLE_ROOT" > run_manifest.txt

if [[ -n "${NEKTAR_ENV_FILE:-}" ]]; then
    source "$NEKTAR_ENV_FILE"
fi

python3 "$BUNDLE_ROOT/scripts/preflight.py" --check-programs | tee preflight_programs.log

python3 "$BUNDLE_ROOT/geometry/generate_geo.py" \
    --output diamond.geo \
    --rfar "$RFAR" \
    --h-wall "$H_WALL" \
    --bl-thickness "$BL_THICKNESS" \
    --bl-ratio "$BL_RATIO" \
    --h-body "$H_BODY" \
    --h-near "$H_NEAR" \
    --h-far "$H_FAR"

python3 "$BUNDLE_ROOT/scripts/preflight.py" --geo diamond.geo | tee -a preflight_geometry.log
gmsh -2 diamond.geo -format msh2 -order 2 -o diamond2d.msh 2>&1 | tee gmsh.log
NekMesh diamond2d.msh mesh2d.xml 2>&1 | tee nekmesh_2d.log
NekMesh \
    -m "extrude:layers=${NZ}:length=${LZ}" \
    -m "peralign:surf1=103:surf2=104:dir=z" \
    mesh2d.xml mesh3d.xml 2>&1 | tee nekmesh_extrude.log

INFO_START=$(( NSTEPS_START / 20 ))
INFO_MAIN=$(( NSTEPS_MAIN / 50 ))
if (( INFO_START < 1 )); then INFO_START=1; fi
if (( INFO_MAIN < 1 )); then INFO_MAIN=1; fi

python3 "$BUNDLE_ROOT/scripts/render_session.py" \
    --template "$BUNDLE_ROOT/templates/session.xml.in" \
    --output stage_start.xml \
    --alpha "$ALPHA_DEG" --order "$ORDER" --lz "$LZ" \
    --dt "$DT_START" --steps "$NSTEPS_START" --time 0 \
    --pert-amp "$PERT_AMP" --av-mu0 "$AV_MU0" \
    --force-file forces_start --force-frequency "$FORCE_FREQ" --force-start 0 \
    --check-file checkpoint_start --check-frequency "$CHECK_FREQ_START" \
    --info-frequency "$INFO_START"

python3 "$BUNDLE_ROOT/scripts/preflight.py" --mesh mesh3d.xml --session stage_start.xml | tee preflight_case.log

run_solver() {
    local session=$1
    if [[ -n "${SLURM_JOB_ID:-}" ]]; then
        srun --cpu-bind=cores CompressibleFlowSolver mesh3d.xml "$session"
    else
        mpirun -np "${NPROCS:-4}" CompressibleFlowSolver mesh3d.xml "$session"
    fi
}

run_solver stage_start.xml 2>&1 | tee solver_start.log
mapfile -t START_CHECKPOINTS < <(find "$RUN_DIR" -maxdepth 1 -type f -name 'checkpoint_start_*.chk' -printf '%p\n' | sort -V)
if (( ${#START_CHECKPOINTS[@]} == 0 )); then
    echo "no startup checkpoint was written" >&2
    exit 1
fi
RESTART_FILE=${START_CHECKPOINTS[-1]}
START_TIME=$(python3 -c "print(float('$DT_START')*int('$NSTEPS_START'))")

python3 "$BUNDLE_ROOT/scripts/render_session.py" \
    --template "$BUNDLE_ROOT/templates/session.xml.in" \
    --output stage_main.xml \
    --alpha "$ALPHA_DEG" --order "$ORDER" --lz "$LZ" \
    --dt "$DT_MAIN" --steps "$NSTEPS_MAIN" --time "$START_TIME" \
    --restart "$RESTART_FILE" --pert-amp "$PERT_AMP" --av-mu0 "$AV_MU0" \
    --force-file forces_main --force-frequency "$FORCE_FREQ" --force-start "$AVG_START" \
    --check-file checkpoint_main --check-frequency "$CHECK_FREQ_MAIN" \
    --info-frequency "$INFO_MAIN"

python3 "$BUNDLE_ROOT/scripts/preflight.py" --session stage_main.xml | tee -a preflight_case.log
run_solver stage_main.xml 2>&1 | tee solver_main.log

python3 "$BUNDLE_ROOT/scripts/scan_solver_log.py" solver_start.log solver_main.log | tee solver_log_check.log
python3 "$BUNDLE_ROOT/post/analyze_forces.py" forces_main.fce \
    --alpha "$ALPHA_DEG" --span "$LZ" --window "$AVG_WINDOW" --output-dir "$RUN_DIR"

echo "run completed: $RUN_DIR"
