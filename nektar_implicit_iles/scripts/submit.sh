#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 {smoke|pilot|production|span_sensitivity} {0|4|8}" >&2
    exit 2
fi

PROFILE_NAME=$1
ALPHA_DEG=$2
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BUNDLE_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
PROFILE_FILE="$BUNDLE_ROOT/profiles/${PROFILE_NAME}.env"

if [[ ! -f "$PROFILE_FILE" ]]; then
    echo "unknown profile: $PROFILE_NAME" >&2
    exit 2
fi
if [[ "$ALPHA_DEG" != "0" && "$ALPHA_DEG" != "4" && "$ALPHA_DEG" != "8" ]]; then
    echo "alpha must be 0, 4, or 8" >&2
    exit 2
fi

set -a
source "$PROFILE_FILE"
set +a

mkdir -p "$BUNDLE_ROOT/runs"
cd "$BUNDLE_ROOT"
sbatch \
    --job-name="nek_${PROFILE_NAME}_a${ALPHA_DEG}" \
    --nodes="$SLURM_NODES" \
    --ntasks-per-node="$SLURM_TASKS_PER_NODE" \
    --time="$SLURM_TIME" \
    --export="ALL,PROFILE_NAME=${PROFILE_NAME},ALPHA_DEG=${ALPHA_DEG}" \
    slurm/run.slurm

