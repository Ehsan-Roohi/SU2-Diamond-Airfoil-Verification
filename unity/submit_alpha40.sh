#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
seed_zip="${URANS_SEED_ZIP:-$repo_root/unity/assets/URANS_alpha40_seed_checkpoint_iter20000.zip}"
storage_root="${URANS_STORAGE_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}"
run_root="${URANS_RUN_ROOT:-$storage_root/runs/urans_alpha40/medium_halfdt}"
checkpoint_dir="${URANS_CHECKPOINT_DIR:-$storage_root/checkpoints/urans_alpha40/medium_halfdt}"
su2_home="${URANS_SU2_HOME:-$storage_root/software/su2-8.5.0-omp}"
log_dir="${URANS_LOG_DIR:-$storage_root/slurm_logs}"
input_dir="$storage_root/inputs"
resume_parts="$repo_root/unity/assets/URANS_alpha40_medium_halfdt_checkpoint_t000664.zip.parts"
resume_zip="${URANS_RESUME_ZIP:-$input_dir/URANS_alpha40_medium_halfdt_checkpoint_t000664.zip}"

if ! command -v sbatch >/dev/null 2>&1 || ! command -v squeue >/dev/null 2>&1; then
  echo "FAILED_GATE: this command must be run on a Unity login node with Slurm." >&2
  exit 2
fi
if [[ ! -f "$seed_zip" ]]; then
  echo "FAILED_GATE: missing $seed_zip" >&2
  echo "The GitHub checkout is incomplete; update branch agent/unity-urans-alpha40 and run again." >&2
  exit 2
fi
seed_zip="$(readlink -f "$seed_zip")"
if [[ "$storage_root" == "$HOME" || "$storage_root" == "$HOME/"* ]]; then
  echo "FAILED_GATE: URANS storage must not be placed under HOME." >&2
  exit 2
fi
mkdir -p "$run_root" "$checkpoint_dir" "$su2_home" "$log_dir" "$input_dir"
storage_root="$(readlink -f "$storage_root")"
run_root="$(readlink -f "$run_root")"
checkpoint_dir="$(readlink -f "$checkpoint_dir")"
su2_home="$(readlink -f "$su2_home")"
log_dir="$(readlink -f "$log_dir")"

if [[ -z "${URANS_RESUME_ZIP:-}" ]]; then
  python3 "$repo_root/scripts/unity_alpha40.py" assemble-resume \
    --parts-dir "$resume_parts" \
    --output "$resume_zip" >/dev/null
elif [[ ! -f "$resume_zip" ]]; then
  echo "FAILED_GATE: missing custom resume checkpoint $resume_zip" >&2
  exit 2
fi
resume_zip="$(readlink -f "$resume_zip")"
for path in "$storage_root" "$run_root" "$checkpoint_dir" "$su2_home" "$log_dir" "$resume_zip"; do
  if [[ "$path" == "$HOME" || "$path" == "$HOME/"* ]]; then
    echo "FAILED_GATE: generated URANS data must not be placed under HOME: $path" >&2
    exit 2
  fi
done
resume_args=(--resume-checkpoint "$resume_zip")

python3 "$repo_root/scripts/unity_alpha40.py" preflight \
  --repo-root "$repo_root" \
  --seed "$seed_zip" \
  "${resume_args[@]}" >/dev/null

active_jobs="$(squeue --me --noheader --name=urans-a40-prj --format='%A' | tr '\n' ' ' | xargs)"
if [[ -n "$active_jobs" ]]; then
  echo "RUNNING: existing Unity job(s): $active_jobs"
  exit 0
fi

job_id="$(sbatch --parsable \
  --chdir="$storage_root" \
  --output="$log_dir/slurm-%x-%j.out" \
  --error="$log_dir/slurm-%x-%j.out" \
  --export="ALL,URANS_REPO_ROOT=$repo_root,URANS_SEED_ZIP=$seed_zip,URANS_RESUME_ZIP=${resume_zip:-},URANS_RUN_ROOT=$run_root,URANS_CHECKPOINT_DIR=$checkpoint_dir,URANS_SU2_HOME=$su2_home" \
  "$repo_root/unity/urans_alpha40.slurm")"
echo "RUNNING: submitted Unity job $job_id"
echo "Storage: $storage_root"
echo "Resume source: ${resume_zip:-steady seed}"
echo "Monitor: squeue -j $job_id && tail -F '$log_dir/slurm-urans-a40-prj-$job_id.out'"
