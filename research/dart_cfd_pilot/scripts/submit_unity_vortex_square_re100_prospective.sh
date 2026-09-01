#!/usr/bin/env bash
#SBATCH --job-name=vortex-sq-r100
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=logs/vortex-sq-r100-%j.out
#SBATCH --error=logs/vortex-sq-r100-%j.err
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
readonly SIMULATION_DIR="${WORK_ROOT}/square-re100-prospective-${JOB_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_TSA_SRA_CMCD_SQUARE_RE100_${JOB_ID}_COMPLETE.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"
readonly RUNNER="${PROJECT_ROOT}/research/dart_cfd_pilot/scripts/run_vortex_cylinder_wake_validation.py"
readonly CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_square_cylinder_re100_prospective_holdout.json"
readonly SPATIAL_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_scale_adaptive_sra_cmcd.json"
readonly TEMPORAL_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_temporal_sa_sra_cmcd.json"
readonly DETECTOR_FREEZE_COMMIT="7d9b27753dde34787c0689168dc5c58fa7a1b1ad"
readonly LOCAL_PREEXECUTION_PROTOCOL_FREEZE_COMMIT="cf98501f6e79c7052a6ad48e9f9e8e680744d265"
readonly PUBLISHED_PROTOCOL_RECORD_COMMIT="b6a782f772cb1da64096f2f339532d2ed296ad6c"

mkdir -p "${PROJECT_ROOT}/logs" "${OUTPUT_DIR}" "${SIMULATION_DIR}" \
  "${WORK_ROOT}/matplotlib" "${WORK_ROOT}/pip-cache"
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
git cat-file -e "${PUBLISHED_PROTOCOL_RECORD_COMMIT}^{commit}"
"${PYTHON_BIN}" -m pytest -q \
  research/dart_cfd_pilot/tests/test_vortex_cylinder_wake_validation.py \
  research/dart_cfd_pilot/tests/test_vortex_temporal_cylinder_validation.py \
  research/dart_cfd_pilot/tests/test_vortex_analytic_positive_control.py \
  research/dart_cfd_pilot/tests/test_vortex_shock_ridge_aware.py

set +e
"${PYTHON_BIN}" "${RUNNER}" \
  --config "${CONFIG}" \
  --sra-config "${SPATIAL_CONFIG}" \
  --temporal-config "${TEMPORAL_CONFIG}" \
  --simulation-dir "${SIMULATION_DIR}" \
  --output-dir "${OUTPUT_DIR}"
RUN_RC=$?
set -e
if [[ "${RUN_RC}" -ne 0 && "${RUN_RC}" -ne 5 ]]; then
  echo "ERROR: prospective Re=100 run ended with technical rc=${RUN_RC}" >&2
  exit "${RUN_RC}"
fi

{
  echo "job_id=${JOB_ID}"
  echo "git_commit=$(git rev-parse HEAD)"
  echo "detector_freeze_commit=${DETECTOR_FREEZE_COMMIT}"
  echo "local_preexecution_protocol_freeze_commit=${LOCAL_PREEXECUTION_PROTOCOL_FREEZE_COMMIT}"
  echo "published_protocol_record_commit=${PUBLISHED_PROTOCOL_RECORD_COMMIT}"
  echo "scientific_rc=${RUN_RC}"
  echo "host=$(hostname)"
  "${PYTHON_BIN}" --version
  cc --version | head -n 1
} > "${OUTPUT_DIR}/square_re100_environment.txt"
cp "${PROJECT_ROOT}/research/dart_cfd_pilot/SQUARE_CYLINDER_REFERENCE_AUDIT.md" "${OUTPUT_DIR}/"
tar --no-same-owner -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"

echo "VORTEX_SQUARE_RE100_RC=${RUN_RC}"
echo "VORTEX_SQUARE_RE100_STATUS=completed"
echo "VORTEX_SQUARE_RE100_ARCHIVE=${ARCHIVE}"
echo "VORTEX_SQUARE_RE100_CHECKSUM=${CHECKSUM}"
exit 0
