#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
storage_root="${SU2_A048_STORAGE_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}"
su2_home="${SU2_A048_SU2_HOME:-$storage_root/software/su2-8.5.0-omp}"
log_dir="${SU2_A048_LOG_DIR:-$storage_root/slurm_logs}"
archive_dir="${SU2_A048_ARCHIVE_DIR:-$storage_root/artifacts/euler_a048_full}"

if ! command -v sbatch >/dev/null 2>&1 || ! command -v squeue >/dev/null 2>&1; then
  echo "FAILED_GATE: run this submitter on a Unity login node." >&2
  exit 2
fi
for required in \
  "$repo_root/meshes/diamond_euler_sharp_medium_720x181.su2" \
  "$repo_root/scripts/run_case.py" \
  "$repo_root/scripts/package_euler_a048_full.py" \
  "$repo_root/unity/euler_a048_full.slurm"
do
  [[ -s "$required" ]] || { echo "FAILED_GATE: missing $required" >&2; exit 2; }
done
if [[ "$storage_root" == "$HOME" || "$storage_root" == "$HOME/"* ]]; then
  echo "FAILED_GATE: dataset storage must be under Unity project storage, not HOME." >&2
  exit 2
fi
mkdir -p "$storage_root/runs/euler_a048_full" "$archive_dir" "$su2_home" "$log_dir"
storage_root="$(readlink -f "$storage_root")"
archive_dir="$(readlink -f "$archive_dir")"
su2_home="$(readlink -f "$su2_home")"
log_dir="$(readlink -f "$log_dir")"

active_jobs="$(squeue --me --noheader --name=su2-a048-full --format='%A' | tr '\n' ' ' | xargs)"
if [[ -n "$active_jobs" ]]; then
  echo "RUNNING: existing Unity job(s): $active_jobs"
  exit 0
fi

commit="$(git -C "$repo_root" rev-parse --short=12 HEAD)"
run_tag="${SU2_A048_RUN_TAG:-$(date -u +%Y%m%dT%H%M%SZ)_$commit}"
run_root="$storage_root/runs/euler_a048_full/$run_tag"
mkdir -p "$run_root"
run_root="$(readlink -f "$run_root")"

job_id="$(sbatch --parsable \
  --chdir="$storage_root" \
  --output="$log_dir/slurm-su2-a048-full-%j.out" \
  --error="$log_dir/slurm-su2-a048-full-%j.out" \
  --export="ALL,A048_REPO_ROOT=$repo_root,A048_RUN_ROOT=$run_root,A048_ARCHIVE_DIR=$archive_dir,A048_SU2_HOME=$su2_home" \
  "$repo_root/unity/euler_a048_full.slurm")"

{
  echo "job_id=$job_id"
  echo "submitted_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "repo_commit=$(git -C "$repo_root" rev-parse HEAD)"
  echo "run_root=$run_root"
  echo "archive_dir=$archive_dir"
} > "$run_root/SUBMISSION.txt"

echo "RUNNING: submitted Unity job $job_id"
echo "RUN_ROOT=$run_root"
echo "ARCHIVE_DIR=$archive_dir"
echo "MONITOR=squeue -j $job_id"
echo "LOG=$log_dir/slurm-su2-a048-full-$job_id.out"
