#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
batch_file="${repo_dir}/su2_unsteady_euler/unity_su2_unsteady_cpu.sbatch"

python "${repo_dir}/su2_unsteady_euler/generate_wakefine_ogrid.py" --validate-only
bash -n "${batch_file}"

echo "Submitting SU2 v8.5 unsteady Euler: M=3, alpha=30 deg, grid=1440x721, dt=2e-6 s, steps=4000"
sbatch \
    --export=ALL,SU2_REPO_DIR="${repo_dir}",SU2_CFD_BIN="${SU2_CFD_BIN:-}" \
    "${batch_file}"
