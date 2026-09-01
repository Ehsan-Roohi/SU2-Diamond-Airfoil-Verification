#!/usr/bin/env bash
#SBATCH --job-name=vortex-apc
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=logs/vortex-apc-%j.out
#SBATCH --error=logs/vortex-apc-%j.err
#SBATCH --mail-type=END,FAIL

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly SOURCE_PROJECT_ROOT
readonly PROJECT_ROOT="${VORTEX_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${VORTEX_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly PYTHON_BIN="${VORTEX_APC_PYTHON:-${WORK_ROOT}/conda/dart-sam3-py311/bin/python}"
readonly REQUIREMENTS="${PROJECT_ROOT}/research/dart_cfd_pilot/requirements-shock-ridge-aware.txt"
readonly JOB_ID="${SLURM_JOB_ID:-manual}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${JOB_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_ANALYTIC_PC_${JOB_ID}_COMPLETE.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"

mkdir -p "${PROJECT_ROOT}/logs" "${OUTPUT_DIR}" "${WORK_ROOT}/matplotlib" "${WORK_ROOT}/pip-cache"
export PIP_CACHE_DIR="${WORK_ROOT}/pip-cache"
if ! PYTHONNOUSERSITE=1 "${PYTHON_BIN}" -c 'import matplotlib.pyplot, numpy, pytest, scipy'; then
  PYTHONNOUSERSITE=1 "${PYTHON_BIN}" -m pip install --disable-pip-version-check --no-input --upgrade --requirement "${REQUIREMENTS}"
fi
export PYTHONNOUSERSITE=1
export MPLCONFIGDIR="${WORK_ROOT}/matplotlib"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
cd "${PROJECT_ROOT}"

"${PYTHON_BIN}" -m pytest -q research/dart_cfd_pilot/tests/test_vortex_analytic_positive_control.py
set +e
"${PYTHON_BIN}" research/dart_cfd_pilot/scripts/run_vortex_analytic_positive_control.py --output-dir "${OUTPUT_DIR}"
RC=$?
set -e
{
  echo "job_id=${JOB_ID}"
  echo "method=frozen_SRA_CMCD_analytic_positive_control"
  echo "git_commit=$(git rev-parse HEAD)"
  "${PYTHON_BIN}" --version
} > "${OUTPUT_DIR}/analytic_positive_control_environment.txt"
tar --no-same-owner -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"
echo "VORTEX_ANALYTIC_PC_RC=${RC}"
echo "VORTEX_ANALYTIC_PC_ARCHIVE=${ARCHIVE}"
echo "VORTEX_ANALYTIC_PC_CHECKSUM=${CHECKSUM}"
exit "${RC}"
