#!/usr/bin/env bash
#SBATCH --job-name=dart-cfd-s4
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=00:30:00
#SBATCH --output=logs/dart-cfd-s4-%j.out
#SBATCH --error=logs/dart-cfd-s4-%j.err
#SBATCH --mail-type=END,FAIL

set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${DART_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${DART_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly ENV_PREFIX="${WORK_ROOT}/conda/dart-sam3-py311"
readonly RUN_ID="${SLURM_JOB_ID:-stage4-manual}"
readonly OUTPUT_REL="results/${RUN_ID}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/${OUTPUT_REL}"
readonly ARCHIVE="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${RUN_ID}.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"

if [[ -z "${DART_STAGE4_STAGE3_DIR:-}" ]]; then
    echo "ERROR: set DART_STAGE4_STAGE3_DIR to a completed Stage-3 JOBID directory." >&2
    exit 2
fi
if [[ ! -f "${DART_STAGE4_STAGE3_DIR}/stage3_report.json" || ! -f "${DART_STAGE4_STAGE3_DIR}/stage3_tracks.csv" ]]; then
    echo "ERROR: Stage-3 report/tracks not found under ${DART_STAGE4_STAGE3_DIR}" >&2
    exit 3
fi
if [[ ! -x "${ENV_PREFIX}/bin/python" ]]; then
    echo "ERROR: reusable Python environment is absent under ${ENV_PREFIX}." >&2
    exit 4
fi

mkdir -p "${OUTPUT_DIR}" "${PROJECT_ROOT}/logs"
module purge
module load conda/latest
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${ENV_PREFIX}"

cd "${PROJECT_ROOT}"
python -m pytest -q research/dart_cfd_pilot/tests

args=(
    --stage3-dir "${DART_STAGE4_STAGE3_DIR}"
    --output-dir "${OUTPUT_REL}"
)
if [[ -n "${DART_STAGE4_REFERENCE_CSV:-}" ]]; then
    args+=(--reference-csv "${DART_STAGE4_REFERENCE_CSV}")
fi

python research/dart_cfd_pilot/scripts/run_dart_stage4_validation.py "${args[@]}"

{
    echo "slurm_job_id=${SLURM_JOB_ID:-manual}"
    echo "host=${HOSTNAME}"
    echo "project_commit=$(git rev-parse HEAD)"
    echo "stage3_directory=${DART_STAGE4_STAGE3_DIR}"
    echo "reference_csv=${DART_STAGE4_REFERENCE_CSV:-not_supplied}"
    python --version
} > "${OUTPUT_DIR}/stage4_environment.txt"

tar -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"
echo "DART_STAGE4_RC=0"
echo "DART_STAGE4_OUTPUT_DIR=${OUTPUT_DIR}"
echo "DART_STAGE4_ARCHIVE=${ARCHIVE}"
echo "DART_STAGE4_CHECKSUM=${CHECKSUM}"
