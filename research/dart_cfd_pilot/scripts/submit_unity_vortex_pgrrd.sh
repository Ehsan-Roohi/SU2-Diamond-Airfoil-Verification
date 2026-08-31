#!/usr/bin/env bash
#SBATCH --job-name=vortex-pgrrd
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=logs/vortex-pgrrd-%j.out
#SBATCH --error=logs/vortex-pgrrd-%j.err
#SBATCH --mail-type=END,FAIL,TIME_LIMIT_80

set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${DART_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${DART_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly MFC_ROOT="${VORTEX_PGRRD_MFC_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
readonly CASE_DIR="${VORTEX_PGRRD_MFC_CASE_DIR:-${WORK_ROOT}/stage5-mfc-raw}"
readonly REFERENCE_CATALOGUE="${VORTEX_PGRRD_REFERENCE_CATALOGUE:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63792493/stage8_catalogue.csv}"
readonly BASELINE_DIR="${VORTEX_PGRRD_BASELINE_DIR:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63809195}"
readonly GAMMA_DIR="${VORTEX_PGRRD_GAMMA_DIR:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63809739}"
readonly RUN_ID="${SLURM_JOB_ID:-pgrrd-manual}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${RUN_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_PGRRD_${RUN_ID}_COMPLETE.tar.gz"
readonly PYTHON="${MFC_ROOT}/build/venv/bin/python3"

[[ -x "${PYTHON}" ]] || { echo "ERROR: pinned MFC Python not found: ${PYTHON}" >&2; exit 2; }
grep -qx 'status=PASS' "${CASE_DIR}/RUN_OK_RAW_FIELDS.txt" || { echo "ERROR: valid raw completion marker missing" >&2; exit 2; }
[[ -s "${REFERENCE_CATALOGUE}" ]] || { echo "ERROR: reference catalogue missing" >&2; exit 2; }
[[ -s "${BASELINE_DIR}/stage14_report.json" ]] || { echo "ERROR: calibrated-baseline report missing" >&2; exit 2; }
[[ -s "${GAMMA_DIR}/stage15_report.json" ]] || { echo "ERROR: variable-Gamma report missing" >&2; exit 2; }
mkdir -p "${OUTPUT_DIR}" "${PROJECT_ROOT}/logs"

PYTHONPATH="${MFC_ROOT}/toolchain${PYTHONPATH:+:${PYTHONPATH}}" "${PYTHON}" \
  research/dart_cfd_pilot/scripts/run_vortex_pgrrd.py \
  --case-dir "${CASE_DIR}" \
  --mfc-root "${MFC_ROOT}" \
  --reference-catalogue "${REFERENCE_CATALOGUE}" \
  --baseline-report "${BASELINE_DIR}/stage14_report.json" \
  --gamma-report "${GAMMA_DIR}/stage15_report.json" \
  --config research/dart_cfd_pilot/vortex_pgrrd.json \
  --output-dir "${OUTPUT_DIR}"

tar -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${ARCHIVE}.sha256.txt"
echo "PGRRD_RC=0"
echo "PGRRD_OUTPUT_DIR=${OUTPUT_DIR}"
echo "PGRRD_ARCHIVE=${ARCHIVE}"
echo "PGRRD_CHECKSUM=${ARCHIVE}.sha256.txt"
