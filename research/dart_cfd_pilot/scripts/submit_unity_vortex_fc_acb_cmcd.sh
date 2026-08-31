#!/usr/bin/env bash
#SBATCH --job-name=vortex-fcacb
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=logs/vortex-fcacb-%j.out
#SBATCH --error=logs/vortex-fcacb-%j.err
#SBATCH --mail-type=END,FAIL

set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${VORTEX_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${VORTEX_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly MFC_ROOT="${VORTEX_FCACB_MFC_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
readonly CASE_DIR="${VORTEX_FCACB_MFC_CASE_DIR:-${WORK_ROOT}/ccfcv-alpha30-raw}"
readonly CCFCV_DIR="${VORTEX_FCACB_CCFCV_DIR:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63828431}"
readonly TEST_PYTHON="${WORK_ROOT}/conda/dart-sam3-py311/bin/python"
readonly MFC_PYTHON="${MFC_ROOT}/build/venv/bin/python3"
readonly RUN_ID="${SLURM_JOB_ID:-fc-acb-cmcd-manual}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${RUN_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_FC_ACB_CMCD_${RUN_ID}_COMPLETE.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"

[[ -x "${MFC_PYTHON}" ]] || { echo "ERROR: pinned MFC Python missing" >&2; exit 2; }
[[ -x "${TEST_PYTHON}" ]] || { echo "ERROR: test environment missing" >&2; exit 3; }
[[ -f "${CASE_DIR}/RUN_OK_CCFCV_RAW_FIELDS.txt" ]] || { echo "ERROR: completed alpha-30 raw marker missing" >&2; exit 4; }
[[ -f "${CCFCV_DIR}/ccfcv_report.json" && -f "${CCFCV_DIR}/ccfcv_reference_catalogue.csv" ]] || {
    echo "ERROR: completed CC-FCV result missing" >&2
    exit 5
}

mkdir -p "${OUTPUT_DIR}" "${PROJECT_ROOT}/logs"
module purge
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-8}"
export OPENBLAS_NUM_THREADS="${OMP_NUM_THREADS}"
export MKL_NUM_THREADS="${OMP_NUM_THREADS}"

cd "${PROJECT_ROOT}"
"${TEST_PYTHON}" -m pytest -q research/dart_cfd_pilot/tests/test_vortex_fc_acb_cmcd.py
PYTHONPATH="${MFC_ROOT}/toolchain${PYTHONPATH:+:${PYTHONPATH}}" \
    "${MFC_PYTHON}" research/dart_cfd_pilot/scripts/run_vortex_acb_cmcd.py \
    --case-dir "${CASE_DIR}" --mfc-root "${MFC_ROOT}" --ccfcv-dir "${CCFCV_DIR}" \
    --config research/dart_cfd_pilot/vortex_fc_acb_cmcd.json --output-dir "${OUTPUT_DIR}"

{
    echo "slurm_job_id=${SLURM_JOB_ID:-manual}"
    echo "host=${HOSTNAME}"
    echo "project_commit=$(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
    echo "raw_case_dir=${CASE_DIR}"
    echo "ccfcv_result_dir=${CCFCV_DIR}"
    echo "execution=cpu_only_existing_raw_fields"
    echo "protocol=calibration_feasibility_constrained_temporal_holdout"
} > "${OUTPUT_DIR}/fc_acb_cmcd_environment.txt"

tar --no-same-owner -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"
echo "FC_ACB_CMCD_RC=0"
echo "FC_ACB_CMCD_OUTPUT_DIR=${OUTPUT_DIR}"
echo "FC_ACB_CMCD_ARCHIVE=${ARCHIVE}"
echo "FC_ACB_CMCD_CHECKSUM=${CHECKSUM}"
