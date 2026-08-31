#!/usr/bin/env bash
#SBATCH --job-name=vortex-s15
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=logs/vortex-s15-%j.out
#SBATCH --error=logs/vortex-s15-%j.err
#SBATCH --mail-type=END,FAIL,TIME_LIMIT_80

set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${DART_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${DART_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly MFC_ROOT="${DART_STAGE15_MFC_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
readonly CASE_DIR="${DART_STAGE15_MFC_CASE_DIR:-${WORK_ROOT}/stage5-mfc-raw}"
readonly STAGE8_CATALOGUE="${DART_STAGE15_STAGE8_CATALOGUE:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63792493/stage8_catalogue.csv}"
readonly STAGE13_DIR="${DART_STAGE15_STAGE13_DIR:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63806843}"
readonly STAGE14_DIR="${DART_STAGE15_STAGE14_DIR:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63809195}"
readonly RUN_ID="${SLURM_JOB_ID:-stage15-manual}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${RUN_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/STAGE15_VORTEX_${RUN_ID}_COMPLETE.tar.gz"
readonly PYTHON="${MFC_ROOT}/build/venv/bin/python3"

[[ -x "${PYTHON}" ]] || { echo "ERROR: pinned MFC Python not found: ${PYTHON}" >&2; exit 2; }
grep -qx 'status=PASS' "${CASE_DIR}/RUN_OK_RAW_FIELDS.txt" || { echo "ERROR: valid raw completion marker missing" >&2; exit 2; }
[[ -s "${STAGE8_CATALOGUE}" ]] || { echo "ERROR: Stage 8 catalogue missing" >&2; exit 2; }
[[ -s "${STAGE13_DIR}/stage13_detections.csv" ]] || { echo "ERROR: Stage 13 detections missing" >&2; exit 2; }
[[ -s "${STAGE14_DIR}/stage14_report.json" ]] || { echo "ERROR: Stage 14 report missing" >&2; exit 2; }
mkdir -p "${OUTPUT_DIR}" "${PROJECT_ROOT}/logs"

PYTHONPATH="${MFC_ROOT}/toolchain${PYTHONPATH:+:${PYTHONPATH}}" "${PYTHON}" research/dart_cfd_pilot/scripts/run_vortex_stage15_vgcm.py --case-dir "${CASE_DIR}" --mfc-root "${MFC_ROOT}" --stage8-catalogue "${STAGE8_CATALOGUE}" --stage13-detections "${STAGE13_DIR}/stage13_detections.csv" --stage14-report "${STAGE14_DIR}/stage14_report.json" --config research/dart_cfd_pilot/dart_stage15.json --output-dir "${OUTPUT_DIR}"

tar -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${ARCHIVE}.sha256.txt"
echo "DART_STAGE15_RC=0"
echo "DART_STAGE15_OUTPUT_DIR=${OUTPUT_DIR}"
echo "DART_STAGE15_ARCHIVE=${ARCHIVE}"
echo "DART_STAGE15_CHECKSUM=${ARCHIVE}.sha256.txt"
