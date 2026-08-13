#!/usr/bin/env bash
#SBATCH --job-name=urans-a40-mhdt
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --requeue
#SBATCH --signal=B:USR1@300
#SBATCH --output=slurm-%x-%j.out
#SBATCH --error=slurm-%x-%j.out

set -euo pipefail

repo_root="${URANS_REPO_ROOT:-$SLURM_SUBMIT_DIR}"
seed_zip="${URANS_SEED_ZIP:?URANS_SEED_ZIP is required}"
run_root="${URANS_RUN_ROOT:-$repo_root/unity_runs/urans_alpha40/medium_halfdt}"
threads="${SLURM_CPUS_PER_TASK:-16}"

su2_url="https://github.com/su2code/SU2/releases/download/v8.5.0/SU2-v8.5.0-linux64-omp.zip"
su2_sha256="aadc800cd9df34deff99d4725f5897f620c9f2979f62ab235313311bf501f09b"
su2_install="${URANS_SU2_HOME:-$HOME/.local/opt/su2-8.5.0-omp}"

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
      exit 2
    }
    python3 -m zipfile -e "$archive" "$su2_install"
    # The official v8.5.0 GitHub asset is a wrapper ZIP containing the
    # actual Linux distribution as linux64-omp.zip.  Extract both layers.
    if [[ -f "$nested_archive" ]]; then
      python3 -m zipfile -e "$nested_archive" "$su2_install"
    fi
    found="$(find "$su2_install" -type f -name SU2_CFD -print -quit)"
    [[ -n "$found" ]] || {
      echo "FAILED_GATE: SU2_CFD missing after nested archive extraction" >&2
      find "$su2_install" -maxdepth 2 -type f -printf '%P\n' >&2 || true
      exit 2
    }
  fi
  chmod +x "$found"
  readlink -f "$found"
}

cd "$repo_root"
solver="$(resolve_solver)"
export OMP_NUM_THREADS="$threads"
export OMP_PROC_BIND=spread
export OMP_PLACES=cores
export PYTHONUNBUFFERED=1

runner_pid=""
requeue_requested=0
checkpoint_and_requeue() {
  if [[ "$requeue_requested" -eq 1 ]]; then
    return
  fi
  requeue_requested=1
  if [[ -n "$runner_pid" ]] && kill -0 "$runner_pid" 2>/dev/null; then
    kill -USR1 "$runner_pid" 2>/dev/null || true
    wait "$runner_pid" || true
  fi
  scontrol requeue "$SLURM_JOB_ID"
  exit 0
}
trap checkpoint_and_requeue USR1 TERM

python3 "$repo_root/scripts/unity_alpha40.py" run \
  --repo-root "$repo_root" \
  --run-root "$run_root" \
  --seed "$seed_zip" \
  --solver "$solver" \
  --threads "$threads" \
  --target-step 12000 \
  --chunk-steps 20 &
runner_pid="$!"

set +e
wait "$runner_pid"
runner_status="$?"
set -e
if [[ "$runner_status" -eq 75 ]]; then
  scontrol requeue "$SLURM_JOB_ID"
  exit 0
fi
exit "$runner_status"
