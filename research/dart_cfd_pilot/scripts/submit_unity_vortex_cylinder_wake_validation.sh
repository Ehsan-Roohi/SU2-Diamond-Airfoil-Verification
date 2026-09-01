#!/usr/bin/env bash
#SBATCH --job-name=vortex-cyl
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=logs/vortex-cyl-%j.out
#SBATCH --error=logs/vortex-cyl-%j.err
#SBATCH --mail-type=END,FAIL

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly SOURCE_PROJECT_ROOT
readonly PROJECT_ROOT="${VORTEX_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${VORTEX_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly PYTHON_BIN="${VORTEX_CYLINDER_PYTHON:-${WORK_ROOT}/conda/dart-sam3-py311/bin/python}"
readonly REQUIREMENTS="${PROJECT_ROOT}/research/dart_cfd_pilot/requirements-shock-ridge-aware.txt"
readonly JOB_ID="${SLURM_JOB_ID:-manual}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${JOB_ID}"
readonly SIMULATION_ROOT="${WORK_ROOT}/cylinder-wake-${JOB_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_CYLINDER_WAKE_${JOB_ID}_COMPLETE.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"
readonly RUNNER="${PROJECT_ROOT}/research/dart_cfd_pilot/scripts/run_vortex_cylinder_wake_validation.py"
readonly DEVELOPMENT_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_cylinder_wake_validation.json"
readonly HOLDOUT_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_cylinder_wake_re150_holdout.json"
readonly DETECTOR_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_scale_adaptive_sra_cmcd.json"

mkdir -p \
  "${PROJECT_ROOT}/logs" \
  "${OUTPUT_DIR}/re100_development" \
  "${OUTPUT_DIR}/re150_holdout" \
  "${SIMULATION_ROOT}/re100" \
  "${SIMULATION_ROOT}/re150" \
  "${WORK_ROOT}/matplotlib" \
  "${WORK_ROOT}/pip-cache"

export PYTHONNOUSERSITE=1
export MPLCONFIGDIR="${WORK_ROOT}/matplotlib"
export PIP_CACHE_DIR="${WORK_ROOT}/pip-cache"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"

if ! "${PYTHON_BIN}" -c 'import matplotlib.pyplot, numpy, pytest, scipy'; then
  "${PYTHON_BIN}" -m pip install --disable-pip-version-check --no-input \
    --upgrade --requirement "${REQUIREMENTS}"
fi

cd "${PROJECT_ROOT}"
"${PYTHON_BIN}" -m pytest -q \
  research/dart_cfd_pilot/tests/test_vortex_cylinder_wake_validation.py \
  research/dart_cfd_pilot/tests/test_vortex_analytic_positive_control.py \
  research/dart_cfd_pilot/tests/test_vortex_shock_ridge_aware.py

"${PYTHON_BIN}" "${RUNNER}" \
  --config "${DEVELOPMENT_CONFIG}" \
  --sra-config "${DETECTOR_CONFIG}" \
  --simulation-dir "${SIMULATION_ROOT}/re100" \
  --output-dir "${OUTPUT_DIR}/re100_development"
readonly RE100_RC=$?

set +e
"${PYTHON_BIN}" "${RUNNER}" \
  --config "${HOLDOUT_CONFIG}" \
  --sra-config "${DETECTOR_CONFIG}" \
  --simulation-dir "${SIMULATION_ROOT}/re150" \
  --output-dir "${OUTPUT_DIR}/re150_holdout"
RE150_RC=$?
set -e
readonly RE150_RC

if [[ "${RE100_RC}" -ne 0 ]]; then
  echo "ERROR: Re=100 development gate did not pass (rc=${RE100_RC})" >&2
  exit "${RE100_RC}"
fi
if [[ "${RE150_RC}" -ne 0 && "${RE150_RC}" -ne 5 ]]; then
  echo "ERROR: Re=150 holdout ended with unexpected technical rc=${RE150_RC}" >&2
  exit "${RE150_RC}"
fi

{
  echo "job_id=${JOB_ID}"
  echo "git_commit=$(git rev-parse HEAD)"
  echo "host=$(hostname)"
  echo "nproc=$(nproc)"
  echo "allocated_cpus=${SLURM_CPUS_PER_TASK:-unknown}"
  echo "re100_scientific_rc=${RE100_RC}"
  echo "re150_scientific_rc=${RE150_RC}"
  "${PYTHON_BIN}" --version
  cc --version | head -n 1
} > "${OUTPUT_DIR}/cylinder_wake_environment.txt"

tar --no-same-owner -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"
echo "VORTEX_CYLINDER_RE100_RC=${RE100_RC}"
echo "VORTEX_CYLINDER_RE150_HOLDOUT_RC=${RE150_RC}"
echo "VORTEX_CYLINDER_STATUS=completed"
echo "VORTEX_CYLINDER_ARCHIVE=${ARCHIVE}"
echo "VORTEX_CYLINDER_CHECKSUM=${CHECKSUM}"
exit 0
