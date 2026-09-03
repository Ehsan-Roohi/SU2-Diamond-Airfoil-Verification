#!/usr/bin/env bash
#SBATCH --job-name=su2-a048-full
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --output=slurm-%x-%j.out
#SBATCH --error=slurm-%x-%j.out

set -Eeuo pipefail

repo_root="${A048_REPO_ROOT:?A048_REPO_ROOT is required}"
run_root="${A048_RUN_ROOT:?A048_RUN_ROOT is required}"
archive_dir="${A048_ARCHIVE_DIR:?A048_ARCHIVE_DIR is required}"
su2_install="${A048_SU2_HOME:?A048_SU2_HOME is required}"
threads="${SLURM_CPUS_PER_TASK:-16}"
su2_url="https://github.com/su2code/SU2/releases/download/v8.5.0/SU2-v8.5.0-linux64-omp.zip"
su2_sha256="aadc800cd9df34deff99d4725f5897f620c9f2979f62ab235313311bf501f09b"

status_dir="$run_root/status"
provenance_dir="$run_root/provenance"
mkdir -p "$run_root/cases" "$run_root/meshes" "$status_dir" "$provenance_dir" "$archive_dir"

fail_record() {
  local rc=$?
  {
    echo "FAILED"
    echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "exit_code=$rc"
    echo "job_id=${SLURM_JOB_ID:-unknown}"
  } > "$run_root/RUN_FAILED.txt"
  exit "$rc"
}
trap fail_record ERR

resolve_solver() {
  if [[ -n "${SU2_CFD_BIN:-}" && -x "${SU2_CFD_BIN}" ]]; then
    readlink -f "${SU2_CFD_BIN}"
    return
  fi
  if command -v SU2_CFD >/dev/null 2>&1; then
    command -v SU2_CFD
    return
  fi
  local archive="$su2_install/SU2-v8.5.0-linux64-omp.zip"
  local nested_archive="$su2_install/linux64-omp.zip"
  local found=""
  found="$(find "$su2_install" -type f -name SU2_CFD -print -quit 2>/dev/null || true)"
  if [[ -z "$found" ]]; then
    mkdir -p "$su2_install"
    if [[ ! -f "$archive" ]]; then
      curl --fail --location --retry 3 --output "$archive.tmp" "$su2_url"
      mv "$archive.tmp" "$archive"
    fi
    echo "$su2_sha256  $archive" | sha256sum --check --status || {
      echo "FAILED_GATE: SU2 v8.5.0 archive checksum mismatch" >&2
      return 2
    }
    python3 -m zipfile -e "$archive" "$su2_install"
    if [[ -f "$nested_archive" ]]; then
      python3 -m zipfile -e "$nested_archive" "$su2_install"
    fi
    found="$(find "$su2_install" -type f -name SU2_CFD -print -quit)"
  fi
  [[ -n "$found" ]] || {
    echo "FAILED_GATE: SU2_CFD missing after installation" >&2
    return 2
  }
  chmod +x "$found"
  readlink -f "$found"
}

solver="$(resolve_solver)"
export PATH="$(dirname "$solver"):$PATH"
export OMP_NUM_THREADS="$threads"
export OMP_PROC_BIND=spread
export OMP_PLACES=cores
export PYTHONUNBUFFERED=1

mesh_name="diamond_euler_sharp_medium_720x181.su2"
cp "$repo_root/meshes/$mesh_name" "$run_root/meshes/$mesh_name"
cp "$repo_root/EXPECTED_RESULTS.csv" "$provenance_dir/EXPECTED_RESULTS.csv"
cp "$repo_root/scripts/run_case.py" "$provenance_dir/run_case.py"
cp "$repo_root/scripts/extract_wave_metrics.py" "$provenance_dir/extract_wave_metrics.py"
cp "$repo_root/scripts/package_euler_a048_full.py" "$provenance_dir/package_euler_a048_full.py"

git -C "$repo_root" rev-parse HEAD > "$provenance_dir/repo_commit.txt"
git -C "$repo_root" status --short > "$provenance_dir/repo_status.txt"
sha256sum "$solver" > "$provenance_dir/SU2_CFD.sha256.txt"
"$solver" --help > "$provenance_dir/SU2_CFD_help.txt" 2>&1 || true
python3 --version > "$provenance_dir/python_version.txt" 2>&1
uname -a > "$provenance_dir/system.txt"

{
  echo "dataset=SU2 Euler Mach 3 alpha 0,4,8"
  echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "job_id=${SLURM_JOB_ID:-unknown}"
  echo "host=$(hostname)"
  echo "threads=$threads"
  echo "solver=$solver"
  echo "run_root=$run_root"
} > "$run_root/RUN_STARTED.txt"

for angle in 0 4 8; do
  case_name="euler_alpha${angle}"
  case_dir="$run_root/cases/$case_name"
  mkdir -p "$case_dir"
  cp "$repo_root/cases/$case_name/startup.cfg" "$case_dir/startup.cfg"
  cp "$repo_root/cases/$case_name/second_order.cfg" "$case_dir/second_order.cfg"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] START $case_name"
  set +e
  python3 "$repo_root/scripts/run_case.py" "$case_dir" \
    --threads "$threads" \
    --expected-results "$repo_root/EXPECTED_RESULTS.csv" \
    2>&1 | tee "$status_dir/${case_name}_wrapper.log"
  wrapper_rc="${PIPESTATUS[0]}"
  set -e
  echo "$wrapper_rc" > "$status_dir/$case_name.rc"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] END $case_name wrapper_rc=$wrapper_rc"
done

python3 "$repo_root/scripts/package_euler_a048_full.py" \
  --run-root "$run_root" \
  --archive-dir "$archive_dir" \
  --job-id "${SLURM_JOB_ID:-unknown}"

{
  echo "COMPLETE"
  echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "job_id=${SLURM_JOB_ID:-unknown}"
} > "$run_root/RUN_COMPLETE.txt"

ln -sfn "$run_root" "$(dirname "$run_root")/latest"
latest_archive="$(find "$archive_dir" -maxdepth 1 -type f -name 'SU2_EULER_M3_AOA_0_4_8_FULL_*.zip' -printf '%T@ %p\n' | sort -nr | head -n1 | cut -d' ' -f2-)"
if [[ -n "$latest_archive" ]]; then
  ln -sfn "$latest_archive" "$archive_dir/SU2_EULER_M3_AOA_0_4_8_FULL_LATEST.zip"
fi
echo "A048_COMPLETE run_root=$run_root"
