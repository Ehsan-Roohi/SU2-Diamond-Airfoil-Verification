#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
seed_zip="${URANS_SEED_ZIP:-$HOME/URANS_alpha40_seed_checkpoint_iter20000.zip}"

if ! command -v sbatch >/dev/null 2>&1 || ! command -v squeue >/dev/null 2>&1; then
  echo "FAILED_GATE: this command must be run on a Unity login node with Slurm." >&2
  exit 2
fi
if [[ ! -f "$seed_zip" ]]; then
  echo "FAILED_GATE: missing $seed_zip" >&2
  echo "Download URANS_alpha40_seed_checkpoint_iter20000.zip from ChatGPT Library and upload it to this exact path." >&2
  exit 2
fi
seed_zip="$(readlink -f "$seed_zip")"

python3 "$repo_root/scripts/unity_alpha40.py" preflight \
  --repo-root "$repo_root" \
  --seed "$seed_zip" >/dev/null

active_jobs="$(squeue --me --noheader --name=urans-a40-mhdt --format='%A' | tr '\n' ' ' | xargs)"
if [[ -n "$active_jobs" ]]; then
  echo "RUNNING: existing Unity job(s): $active_jobs"
  exit 0
fi

cd "$repo_root"
job_id="$(sbatch --parsable \
  --export="ALL,URANS_REPO_ROOT=$repo_root,URANS_SEED_ZIP=$seed_zip" \
  unity/urans_alpha40.slurm)"
echo "RUNNING: submitted Unity job $job_id"
echo "Monitor: squeue -j $job_id && tail -F '$repo_root/slurm-urans-a40-mhdt-$job_id.out'"
