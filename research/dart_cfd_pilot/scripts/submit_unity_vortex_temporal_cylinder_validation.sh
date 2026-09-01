#!/usr/bin/env bash
#SBATCH --job-name=vortex-tsa
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=04:00:00
#SBATCH --output=logs/vortex-tsa-%j.out
#SBATCH --error=logs/vortex-tsa-%j.err
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
readonly SIMULATION_ROOT="${WORK_ROOT}/tsa-cylinder-wake-${JOB_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_TSA_SRA_CMCD_CYLINDER_${JOB_ID}_COMPLETE.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"
readonly RUNNER="${PROJECT_ROOT}/research/dart_cfd_pilot/scripts/run_vortex_cylinder_wake_validation.py"
readonly DEVELOPMENT_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_cylinder_wake_re150_temporal_development.json"
readonly HOLDOUT_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_cylinder_wake_re200_temporal_holdout.json"
readonly SPATIAL_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_scale_adaptive_sra_cmcd.json"
readonly TEMPORAL_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_temporal_sa_sra_cmcd.json"
readonly DETECTOR_FREEZE_COMMIT="45b606a73e678085cf11671035d6f651c2e9568f"

mkdir -p \
  "${PROJECT_ROOT}/logs" \
  "${OUTPUT_DIR}/re150_development" \
  "${OUTPUT_DIR}/re200_holdout" \
  "${SIMULATION_ROOT}/re150" \
  "${SIMULATION_ROOT}/re200" \
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
git cat-file -e "${DETECTOR_FREEZE_COMMIT}^{commit}"
"${PYTHON_BIN}" -m pytest -q \
  research/dart_cfd_pilot/tests/test_vortex_cylinder_wake_validation.py \
  research/dart_cfd_pilot/tests/test_vortex_temporal_cylinder_validation.py \
  research/dart_cfd_pilot/tests/test_vortex_analytic_positive_control.py \
  research/dart_cfd_pilot/tests/test_vortex_shock_ridge_aware.py

set +e
"${PYTHON_BIN}" "${RUNNER}" \
  --config "${DEVELOPMENT_CONFIG}" \
  --sra-config "${SPATIAL_CONFIG}" \
  --temporal-config "${TEMPORAL_CONFIG}" \
  --simulation-dir "${SIMULATION_ROOT}/re150" \
  --output-dir "${OUTPUT_DIR}/re150_development"
RE150_RC=$?

"${PYTHON_BIN}" "${RUNNER}" \
  --config "${HOLDOUT_CONFIG}" \
  --sra-config "${SPATIAL_CONFIG}" \
  --temporal-config "${TEMPORAL_CONFIG}" \
  --simulation-dir "${SIMULATION_ROOT}/re200" \
  --output-dir "${OUTPUT_DIR}/re200_holdout"
RE200_RC=$?
set -e
readonly RE150_RC RE200_RC

if [[ "${RE150_RC}" -ne 0 && "${RE150_RC}" -ne 5 ]]; then
  echo "ERROR: Re=150 development ended with technical rc=${RE150_RC}" >&2
  exit "${RE150_RC}"
fi
if [[ "${RE200_RC}" -ne 0 && "${RE200_RC}" -ne 5 ]]; then
  echo "ERROR: Re=200 holdout ended with technical rc=${RE200_RC}" >&2
  exit "${RE200_RC}"
fi

{
  echo "job_id=${JOB_ID}"
  echo "git_commit=$(git rev-parse HEAD)"
  echo "detector_freeze_commit=${DETECTOR_FREEZE_COMMIT}"
  echo "host=$(hostname)"
  echo "allocated_cpus=${SLURM_CPUS_PER_TASK:-unknown}"
  echo "re150_scientific_rc=${RE150_RC}"
  echo "re200_scientific_rc=${RE200_RC}"
  "${PYTHON_BIN}" --version
  cc --version | head -n 1
} > "${OUTPUT_DIR}/tsa_sra_cmcd_environment.txt"

cp \
  "${PROJECT_ROOT}/research/dart_cfd_pilot/CYLINDER_WAKE_VALIDATION.md" \
  "${OUTPUT_DIR}/CYLINDER_WAKE_VALIDATION.md"
tar --no-same-owner -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"

echo "VORTEX_TSA_RE150_RC=${RE150_RC}"
echo "VORTEX_TSA_RE200_HOLDOUT_RC=${RE200_RC}"
echo "VORTEX_TSA_STATUS=completed"
echo "VORTEX_TSA_ARCHIVE=${ARCHIVE}"
echo "VORTEX_TSA_CHECKSUM=${CHECKSUM}"
exit 0
