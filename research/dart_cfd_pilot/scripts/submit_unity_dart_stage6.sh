#!/usr/bin/env bash
#SBATCH --job-name=dart-cfd-s6
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:15:00
#SBATCH --output=logs/dart-cfd-s6-%j.out
#SBATCH --error=logs/dart-cfd-s6-%j.err
#SBATCH --mail-type=END,FAIL

set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${DART_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly ENV_PREFIX="${DART_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}/conda/dart-sam3-py311"
readonly STAGE3_DIR="${DART_STAGE6_STAGE3_DIR:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63761044}"
readonly STAGE5_DIR="${DART_STAGE6_STAGE5_DIR:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63786255}"
readonly RUN_ID="${SLURM_JOB_ID:-stage6-manual}"
readonly OUTPUT_REL="results/${RUN_ID}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/${OUTPUT_REL}"
readonly ARCHIVE="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${RUN_ID}.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"

[[ -x "${ENV_PREFIX}/bin/python" ]] || {
    echo "ERROR: reusable DART Python environment not found: ${ENV_PREFIX}" >&2
    exit 2
}
[[ -f "${STAGE3_DIR}/stage3_report.json" ]] || {
    echo "ERROR: Stage-3 result not found: ${STAGE3_DIR}" >&2
    exit 3
}
[[ -f "${STAGE5_DIR}/stage5_report.json" && -f "${STAGE5_DIR}/stage4_report.json" ]] || {
    echo "ERROR: Stage-5 result not found: ${STAGE5_DIR}" >&2
    exit 4
}

mkdir -p "${OUTPUT_DIR}" "${PROJECT_ROOT}/logs"
cd "${PROJECT_ROOT}"
"${ENV_PREFIX}/bin/python" -m pytest -q research/dart_cfd_pilot/tests
"${ENV_PREFIX}/bin/python" research/dart_cfd_pilot/scripts/run_dart_stage6_audit.py \
    --stage3-dir "${STAGE3_DIR}" \
    --stage5-dir "${STAGE5_DIR}" \
    --output-dir "${OUTPUT_REL}"

{
    echo "slurm_job_id=${SLURM_JOB_ID:-manual}"
    echo "host=${HOSTNAME}"
    echo "project_commit=$(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
    echo "stage3_directory=${STAGE3_DIR}"
    echo "stage5_directory=${STAGE5_DIR}"
} > "${OUTPUT_DIR}/stage6_environment.txt"

tar -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"
echo "DART_STAGE6_RC=0"
echo "DART_STAGE6_OUTPUT_DIR=${OUTPUT_DIR}"
echo "DART_STAGE6_ARCHIVE=${ARCHIVE}"
echo "DART_STAGE6_CHECKSUM=${CHECKSUM}"
