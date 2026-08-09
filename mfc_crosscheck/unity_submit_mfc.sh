#!/usr/bin/env bash
set -euo pipefail

alpha="${1:-30}"
mode="${2:-euler}"
grid="${3:-medium}"
steps="${4:-}"
save_every="${5:-}"

if [[ ! "$alpha" =~ ^-?[0-9]+([.][0-9]+)?$ ]]; then
    echo "ERROR: alpha must be numeric; received '$alpha'." >&2
    exit 2
fi

case "$mode" in
    euler|laminar) ;;
    *) echo "ERROR: mode must be euler or laminar." >&2; exit 2 ;;
esac

case "$grid" in
    smoke|coarse|medium) ;;
    *) echo "ERROR: grid must be smoke, coarse, or medium." >&2; exit 2 ;;
esac

if [[ -n "$steps" && ! "$steps" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: optional steps argument must be a positive integer; received '$steps'." >&2
    exit 2
fi
if [[ -n "$save_every" && ! "$save_every" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: optional save-every argument must be a positive integer; received '$save_every'." >&2
    exit 2
fi

module load apptainer/latest

project_root="${MFC_PROJECT_ROOT:-/project/pi_roohie_umass_edu}"
container_dir="${MFC_CONTAINER_DIR:-${project_root}/containers}"
image="${MFC_IMAGE:-${container_dir}/mfc_latest_cpu.sif}"
export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-${project_root}/.apptainer/cache}"
export APPTAINER_TMPDIR="${APPTAINER_TMPDIR:-${project_root}/.apptainer/tmp}"

mkdir -p "$container_dir" "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"

if [[ ! -s "$image" ]]; then
    echo "Pulling the official MFC CPU image once: $image"
    apptainer pull "$image" docker://sbryngelson/mfc:latest-cpu
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
batch_file="${repo_dir}/mfc_crosscheck/unity_mfc_cpu.sbatch"

echo "Submitting MFC v2: alpha=${alpha}, mode=${mode}, grid=${grid}, steps=${steps:-preset-default}, save_every=${save_every:-preset-default}"
sbatch \
    --export=ALL,MFC_REPO_DIR="$repo_dir",MFC_IMAGE="$image",MFC_ALPHA="$alpha",MFC_MODE="$mode",MFC_GRID="$grid",MFC_STEPS="$steps",MFC_SAVE_EVERY="$save_every" \
    "$batch_file"
