#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --output=nek-%x-%j.out
#SBATCH --error=nek-%x-%j.err
#SBATCH --exclusive

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BUNDLE_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
RUN_DIR="$BUNDLE_ROOT/runs/${PROFILE_NAME}_a${ALPHA_DEG}_${SLURM_JOB_ID}"

mkdir -p "$RUN_DIR"
env | sort > "$RUN_DIR/slurm_environment.txt"
bash "$BUNDLE_ROOT/scripts/run_pipeline.sh" "$PROFILE_NAME" "$ALPHA_DEG" "$RUN_DIR"

