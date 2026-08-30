#!/usr/bin/env bash
#SBATCH --job-name=vortex-s8
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=logs/vortex-s8-%j.out
#SBATCH --error=logs/vortex-s8-%j.err
#SBATCH --mail-type=END,FAIL,TIME_LIMIT_80

set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${DART_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${DART_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly MFC_ROOT="${DART_STAGE8_MFC_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
readonly CASE_DIR="${DART_STAGE8_MFC_CASE_DIR:-${WORK_ROOT}/stage5-mfc-raw}"
readonly MFC_COMMIT="0c9a1d434410175ac483b8d71646455444e3b7eb"
readonly ENV_PREFIX="${WORK_ROOT}/conda/dart-sam3-py311"
readonly RUN_ID="${SLURM_JOB_ID:-stage8-manual}"
readonly OUTPUT_REL="results/${RUN_ID}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/${OUTPUT_REL}"
readonly ARCHIVE="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${RUN_ID}.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"
readonly PYTHON="${MFC_ROOT}/build/venv/bin/python3"

[[ -x "${PYTHON}" ]] || { echo "ERROR: pinned MFC Python not found: ${PYTHON}" >&2; exit 2; }
[[ -x "${ENV_PREFIX}/bin/python" ]] || { echo "ERROR: reusable test environment not found: ${ENV_PREFIX}" >&2; exit 2; }
[[ -f "${CASE_DIR}/RUN_OK_RAW_FIELDS.txt" ]] || { echo "ERROR: completed Stage-5 raw marker not found: ${CASE_DIR}" >&2; exit 3; }
grep -qx 'status=PASS' "${CASE_DIR}/RUN_OK_RAW_FIELDS.txt" || { echo "ERROR: invalid raw completion marker" >&2; exit 4; }
[[ "$(git -C "${MFC_ROOT}" rev-parse HEAD)" == "${MFC_COMMIT}" ]] || { echo "ERROR: MFC revision mismatch" >&2; exit 5; }

mkdir -p "${OUTPUT_DIR}" "${PROJECT_ROOT}/logs"
cd "${PROJECT_ROOT}"
"${ENV_PREFIX}/bin/python" -m pytest -q research/dart_cfd_pilot/tests/test_dart_stage8.py
PYTHONPATH="${MFC_ROOT}/toolchain${PYTHONPATH:+:${PYTHONPATH}}" \
  "${PYTHON}" research/dart_cfd_pilot/scripts/run_dart_stage8_physics_catalogue.py \
  --case-dir "${CASE_DIR}" --mfc-root "${MFC_ROOT}" --output-dir "${OUTPUT_REL}"

{
  echo "slurm_job_id=${SLURM_JOB_ID:-manual}"
  echo "host=${HOSTNAME}"
  echo "project_commit=$(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
  echo "mfc_commit=$(git -C "${MFC_ROOT}" rev-parse HEAD)"
  echo "raw_case_dir=${CASE_DIR}"
} > "${OUTPUT_DIR}/stage8_environment.txt"

tar -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"
echo "DART_STAGE8_RC=0"
echo "DART_STAGE8_OUTPUT_DIR=${OUTPUT_DIR}"
echo "DART_STAGE8_ARCHIVE=${ARCHIVE}"
echo "DART_STAGE8_CHECKSUM=${CHECKSUM}"
