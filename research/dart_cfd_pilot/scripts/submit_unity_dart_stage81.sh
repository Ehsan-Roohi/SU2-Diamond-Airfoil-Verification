#!/usr/bin/env bash
#SBATCH --job-name=vortex-s81
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=logs/vortex-s81-%j.out
#SBATCH --error=logs/vortex-s81-%j.err
#SBATCH --mail-type=END,FAIL,TIME_LIMIT_80
set -Eeuo pipefail
umask 077
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${DART_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${DART_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly MFC_ROOT="${DART_STAGE81_MFC_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
readonly CASE_DIR="${DART_STAGE81_MFC_CASE_DIR:-${WORK_ROOT}/stage5-mfc-raw}"
readonly STAGE8_DIR="${DART_STAGE81_STAGE8_DIR:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63792493}"
readonly ENV_PREFIX="${WORK_ROOT}/conda/dart-sam3-py311"
readonly RUN_ID="${SLURM_JOB_ID:-stage81-manual}"
readonly OUTPUT_REL="results/${RUN_ID}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/${OUTPUT_REL}"
readonly ARCHIVE="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${RUN_ID}.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"
readonly PYTHON="${MFC_ROOT}/build/venv/bin/python3"
[[ -x "${PYTHON}" && -x "${ENV_PREFIX}/bin/python" ]] || { echo "ERROR: required Python environment missing" >&2; exit 2; }
[[ -f "${CASE_DIR}/RUN_OK_RAW_FIELDS.txt" ]] && grep -qx 'status=PASS' "${CASE_DIR}/RUN_OK_RAW_FIELDS.txt" || { echo "ERROR: completed raw sequence missing" >&2; exit 3; }
[[ -f "${STAGE8_DIR}/stage8_catalogue.csv" && -f "${STAGE8_DIR}/stage8_report.json" ]] || { echo "ERROR: Stage-8 result missing: ${STAGE8_DIR}" >&2; exit 4; }
mkdir -p "${OUTPUT_DIR}" "${PROJECT_ROOT}/logs"; cd "${PROJECT_ROOT}"
"${ENV_PREFIX}/bin/python" -m pytest -q research/dart_cfd_pilot/tests/test_dart_stage81.py
PYTHONPATH="${MFC_ROOT}/toolchain${PYTHONPATH:+:${PYTHONPATH}}" "${PYTHON}" research/dart_cfd_pilot/scripts/run_dart_stage81_recall_expansion.py --case-dir "${CASE_DIR}" --mfc-root "${MFC_ROOT}" --stage8-dir "${STAGE8_DIR}" --output-dir "${OUTPUT_REL}"
printf 'slurm_job_id=%s\nhost=%s\nproject_commit=%s\nraw_case_dir=%s\nstage8_dir=%s\n' "${SLURM_JOB_ID:-manual}" "${HOSTNAME}" "$(git -C "${PROJECT_ROOT}" rev-parse HEAD)" "${CASE_DIR}" "${STAGE8_DIR}" > "${OUTPUT_DIR}/stage81_environment.txt"
tar -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .; sha256sum "${ARCHIVE}" > "${CHECKSUM}"
echo "DART_STAGE81_RC=0";echo "DART_STAGE81_OUTPUT_DIR=${OUTPUT_DIR}";echo "DART_STAGE81_ARCHIVE=${ARCHIVE}";echo "DART_STAGE81_CHECKSUM=${CHECKSUM}"
