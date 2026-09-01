#!/usr/bin/env bash
#SBATCH --job-name=vortex-sra
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=logs/vortex-sra-%j.out
#SBATCH --error=logs/vortex-sra-%j.err
#SBATCH --mail-type=END,FAIL

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly SOURCE_PROJECT_ROOT
readonly PROJECT_ROOT="${VORTEX_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${VORTEX_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly CHECKPOINT="${VORTEX_SRA_SU2_CHECKPOINT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data/checkpoints/urans_alpha40/medium_halfdt/URANS_alpha40_medium_halfdt_checkpoint_t012000.zip}"
readonly AA_LOCKED="${VORTEX_SRA_AA_LOCKED_CONFIG:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63862693/artifact_aware_acb_locked_configuration.json}"
readonly CONFIG="${VORTEX_SRA_CONFIG:-${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_shock_ridge_aware_cmcd.json}"
readonly REQUIREMENTS="${PROJECT_ROOT}/research/dart_cfd_pilot/requirements-shock-ridge-aware.txt"
readonly PYTHON_BIN="${VORTEX_SRA_PYTHON:-${WORK_ROOT}/conda/dart-sam3-py311/bin/python}"
readonly JOB_ID="${SLURM_JOB_ID:-manual}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${JOB_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_SHOCK_RIDGE_CMCD_${JOB_ID}_COMPLETE.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"

mkdir -p \
  "${PROJECT_ROOT}/logs" \
  "${OUTPUT_DIR}" \
  "${WORK_ROOT}/matplotlib" \
  "${WORK_ROOT}/pip-cache"
for required in "${CHECKPOINT}" "${AA_LOCKED}" "${CONFIG}" "${REQUIREMENTS}"; do
  if [[ ! -s "${required}" ]]; then
    echo "ERROR: required input missing or empty: ${required}" >&2
    exit 2
  fi
done
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "ERROR: analysis Python is not executable: ${PYTHON_BIN}" >&2
  exit 2
fi

export PIP_CACHE_DIR="${WORK_ROOT}/pip-cache"
if ! PYTHONNOUSERSITE=1 "${PYTHON_BIN}" -c \
  'import matplotlib.pyplot, numpy, pyparsing, pytest, scipy'; then
  echo "Installing the pinned SRA-CMCD analysis stack inside the DART environment"
  PYTHONNOUSERSITE=1 "${PYTHON_BIN}" -m pip install \
    --disable-pip-version-check \
    --no-input \
    --upgrade \
    --requirement "${REQUIREMENTS}"
fi

# Do not allow incomplete packages in ~/.local to shadow the pinned environment.
export PYTHONNOUSERSITE=1
export MPLCONFIGDIR="${WORK_ROOT}/matplotlib"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
cd "${PROJECT_ROOT}"

"${PYTHON_BIN}" -m pytest -q \
  research/dart_cfd_pilot/tests/test_vortex_shock_ridge_aware.py

"${PYTHON_BIN}" research/dart_cfd_pilot/scripts/run_vortex_shock_ridge_aware_su2.py \
  --checkpoint "${CHECKPOINT}" \
  --aa-locked-config "${AA_LOCKED}" \
  --config "${CONFIG}" \
  --output-dir "${OUTPUT_DIR}"

{
  echo "job_id=${JOB_ID}"
  echo "method=SRA-CMCD"
  echo "case_role=SU2_alpha40_unlabelled_development_diagnostic"
  echo "checkpoint=${CHECKPOINT}"
  echo "checkpoint_sha256=$(sha256sum "${CHECKPOINT}" | awk '{print $1}')"
  echo "aa_locked_config=${AA_LOCKED}"
  echo "git_commit=$(git rev-parse HEAD)"
  "${PYTHON_BIN}" --version
  "${PYTHON_BIN}" -c \
    'import matplotlib, numpy, pyparsing, pytest, scipy; print("numpy=" + numpy.__version__); print("scipy=" + scipy.__version__); print("matplotlib=" + matplotlib.__version__); print("pyparsing=" + pyparsing.__version__); print("pytest=" + pytest.__version__)'
} > "${OUTPUT_DIR}/shock_ridge_aware_environment.txt"

tar --no-same-owner -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"

echo "VORTEX_SHOCK_RIDGE_STATUS=completed"
echo "VORTEX_SHOCK_RIDGE_OUTPUT_DIR=${OUTPUT_DIR}"
echo "VORTEX_SHOCK_RIDGE_ARCHIVE=${ARCHIVE}"
echo "VORTEX_SHOCK_RIDGE_CHECKSUM=${CHECKSUM}"
