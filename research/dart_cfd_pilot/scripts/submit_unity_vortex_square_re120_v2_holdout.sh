#!/usr/bin/env bash
#SBATCH --job-name=vortex-sq-v2
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=logs/vortex-sq-v2-%j.out
#SBATCH --error=logs/vortex-sq-v2-%j.err
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
readonly SIMULATION_DIR="${WORK_ROOT}/square-re120-v2-holdout-${JOB_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_TSA_SRA_CMCD_V2_SQUARE_RE120_${JOB_ID}_COMPLETE.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"
readonly RUNNER="${PROJECT_ROOT}/research/dart_cfd_pilot/scripts/run_vortex_cylinder_wake_validation.py"
readonly CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_square_cylinder_re120_v2_holdout.json"
readonly SPATIAL_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_scale_adaptive_sra_cmcd.json"
readonly TEMPORAL_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_temporal_wide_window_tsa_sra_cmcd_v2.json"
readonly LOCAL_PREEXECUTION_FREEZE_COMMIT="0b895f34e05e0c7f990a8ed1a551c4755713dc1c"
readonly PUBLISHED_PROTOCOL_RECORD_COMMIT="b6a782f772cb1da64096f2f339532d2ed296ad6c"
readonly PUBLISHED_PROTOCOL_RECORD_REF="provenance/tsa-sra-cmcd-v2-freeze-record"

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
git fetch origin "${PUBLISHED_PROTOCOL_RECORD_REF}"
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
  echo "ERROR: Re=120 v2 holdout ended with technical rc=${RUN_RC}" >&2
  exit "${RUN_RC}"
fi

{
  echo "job_id=${JOB_ID}"
  echo "git_commit=$(git rev-parse HEAD)"
  echo "local_preexecution_freeze_commit=${LOCAL_PREEXECUTION_FREEZE_COMMIT}"
  echo "published_protocol_record_commit=${PUBLISHED_PROTOCOL_RECORD_COMMIT}"
  echo "scientific_rc=${RUN_RC}"
  echo "host=$(hostname)"
  "${PYTHON_BIN}" --version
  cc --version | head -n 1
} > "${OUTPUT_DIR}/square_re120_v2_environment.txt"
tar --no-same-owner -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"

echo "VORTEX_SQUARE_RE120_V2_RC=${RUN_RC}"
echo "VORTEX_SQUARE_RE120_V2_STATUS=completed"
echo "VORTEX_SQUARE_RE120_V2_ARCHIVE=${ARCHIVE}"
echo "VORTEX_SQUARE_RE120_V2_CHECKSUM=${CHECKSUM}"
exit 0
