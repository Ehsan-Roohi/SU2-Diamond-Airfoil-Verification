#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --output=nek-%x-%j.out
#SBATCH --error=nek-%x-%j.err

set -euo pipefail

BUNDLE_ROOT=${NEKTAR_CASE_ROOT:?NEKTAR_CASE_ROOT is required; submit with scripts/submit.sh}
if [[ "$BUNDLE_ROOT" != /* || ! -f "$BUNDLE_ROOT/scripts/run_pipeline.sh" ]]; then
    echo "invalid NEKTAR_CASE_ROOT: $BUNDLE_ROOT" >&2
    exit 2
fi
RUN_DIR="$BUNDLE_ROOT/runs/${PROFILE_NAME}_a${ALPHA_DEG}_${SLURM_JOB_ID}"

mkdir -p "$RUN_DIR"
env | sort > "$RUN_DIR/slurm_environment.txt"
bash "$BUNDLE_ROOT/scripts/run_pipeline.sh" "$PROFILE_NAME" "$ALPHA_DEG" "$RUN_DIR"
