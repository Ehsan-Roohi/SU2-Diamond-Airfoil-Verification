#!/usr/bin/env bash
#SBATCH --job-name=vortex-aa-acb
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=logs/vortex-aa-acb-%j.out
#SBATCH --error=logs/vortex-aa-acb-%j.err
#SBATCH --mail-type=END,FAIL

set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${VORTEX_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${VORTEX_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly MFC_ROOT="${VORTEX_AAACB_MFC_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
readonly CASE_DIR="${VORTEX_AAACB_MFC_CASE_DIR:-${WORK_ROOT}/ccfcv-alpha30-raw}"
readonly RAW_COMPLETION_MARKER="${VORTEX_AAACB_RAW_COMPLETION_MARKER:-${CASE_DIR}/RUN_OK_CCFCV_RAW_FIELDS.txt}"
readonly CCFCV_DIR="${VORTEX_AAACB_CCFCV_DIR:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63828431}"
readonly ACB_DIR="${VORTEX_AAACB_ACB_DIR:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63834418}"
readonly LABELS="${VORTEX_AAACB_EXPERT_LABELS:-${PROJECT_ROOT}/research/dart_cfd_pilot/reference/acb_cmcd_blind_visual_audit.csv}"
readonly CONFIG="${VORTEX_AAACB_CONFIG:-${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_artifact_aware_acb.json}"
readonly PYTHON_BIN="${VORTEX_AAACB_PYTHON:-${MFC_ROOT}/build/venv/bin/python3}"
readonly JOB_ID="${SLURM_JOB_ID:-manual}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${JOB_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_ARTIFACT_AWARE_ACB_${JOB_ID}_COMPLETE.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"

mkdir -p "${PROJECT_ROOT}/logs" "${OUTPUT_DIR}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "ERROR: MFC Python is not executable: ${PYTHON_BIN}" >&2
  exit 2
fi
if [[ ! -s "${RAW_COMPLETION_MARKER}" ]]; then
  echo "ERROR: CC-FCV raw-field marker missing: ${RAW_COMPLETION_MARKER}" >&2
  exit 2
fi
for expected_marker_line in 'status=PASS' 'alpha_deg=30' 'final_step=16200'; do
  if ! grep -qx "${expected_marker_line}" "${RAW_COMPLETION_MARKER}"; then
    echo "ERROR: CC-FCV raw-field marker lacks ${expected_marker_line}: ${RAW_COMPLETION_MARKER}" >&2
    exit 2
  fi
done
for required in \
  "${CCFCV_DIR}/ccfcv_reference_catalogue.csv" \
  "${ACB_DIR}/acb_cmcd_detections.csv" \
  "${ACB_DIR}/acb_cmcd_blind_key.csv" \
  "${ACB_DIR}/acb_cmcd_locked_configuration.json" \
  "${LABELS}" \
  "${CONFIG}"; do
  if [[ ! -s "${required}" ]]; then
    echo "ERROR: required input missing or empty: ${required}" >&2
    exit 2
  fi
done

cd "${PROJECT_ROOT}"
"${PYTHON_BIN}" -m pytest -q \
  research/dart_cfd_pilot/tests/test_vortex_artifact_aware_acb.py

"${PYTHON_BIN}" research/dart_cfd_pilot/scripts/run_vortex_artifact_aware_acb.py \
  --case-dir "${CASE_DIR}" \
  --mfc-root "${MFC_ROOT}" \
  --ccfcv-dir "${CCFCV_DIR}" \
  --acb-dir "${ACB_DIR}" \
  --expert-labels "${LABELS}" \
  --config "${CONFIG}" \
  --output-dir "${OUTPUT_DIR}"

{
  echo "job_id=${JOB_ID}"
  echo "method=AA-ACB-CMCD"
  echo "case_dir=${CASE_DIR}"
  echo "ccfcv_dir=${CCFCV_DIR}"
  echo "acb_dir=${ACB_DIR}"
  echo "expert_labels=${LABELS}"
  echo "git_commit=$(git rev-parse HEAD)"
  "${PYTHON_BIN}" --version
} > "${OUTPUT_DIR}/artifact_aware_acb_environment.txt"

archive_tmp="${ARCHIVE}.tmp"
tar -C "$(dirname "${OUTPUT_DIR}")" -czf "${archive_tmp}" "$(basename "${OUTPUT_DIR}")"
mv "${archive_tmp}" "${ARCHIVE}"
sha256sum "${ARCHIVE}" > "${CHECKSUM}"

echo "VORTEX_ARTIFACT_AWARE_ACB_STATUS=completed"
echo "VORTEX_ARTIFACT_AWARE_ACB_OUTPUT_DIR=${OUTPUT_DIR}"
echo "VORTEX_ARTIFACT_AWARE_ACB_ARCHIVE=${ARCHIVE}"
echo "VORTEX_ARTIFACT_AWARE_ACB_CHECKSUM=${CHECKSUM}"
