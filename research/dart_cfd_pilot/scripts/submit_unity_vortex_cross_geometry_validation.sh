#!/usr/bin/env bash
#SBATCH --job-name=vortex-xgeom
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=logs/vortex-xgeom-%j.out
#SBATCH --error=logs/vortex-xgeom-%j.err
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
readonly SIMULATION_ROOT="${WORK_ROOT}/tsa-cross-geometry-${JOB_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_TSA_SRA_CMCD_CROSS_GEOMETRY_${JOB_ID}_COMPLETE.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"
readonly RUNNER="${PROJECT_ROOT}/research/dart_cfd_pilot/scripts/run_vortex_cylinder_wake_validation.py"
readonly HOLDOUT_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_square_cylinder_re150_cross_geometry_holdout.json"
readonly SENSITIVITY_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_square_cylinder_re150_blockage_sensitivity.json"
readonly SPATIAL_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_scale_adaptive_sra_cmcd.json"
readonly TEMPORAL_CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_temporal_sa_sra_cmcd.json"
readonly DETECTOR_FREEZE_COMMIT="7d9b27753dde34787c0689168dc5c58fa7a1b1ad"
readonly HOLDOUT_PROTOCOL_FREEZE_COMMIT="acd06a200d7854ed2938fbdbfc529e636a0166bf"

mkdir -p \
  "${PROJECT_ROOT}/logs" \
  "${OUTPUT_DIR}/square_holdout" \
  "${OUTPUT_DIR}/blockage_sensitivity" \
  "${SIMULATION_ROOT}/square_holdout" \
  "${SIMULATION_ROOT}/blockage_sensitivity" \
  "${WORK_ROOT}/matplotlib" \
  "${WORK_ROOT}/pip-cache"

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
git cat-file -e "${HOLDOUT_PROTOCOL_FREEZE_COMMIT}^{commit}"
"${PYTHON_BIN}" -m pytest -q \
  research/dart_cfd_pilot/tests/test_vortex_cylinder_wake_validation.py \
  research/dart_cfd_pilot/tests/test_vortex_temporal_cylinder_validation.py \
  research/dart_cfd_pilot/tests/test_vortex_analytic_positive_control.py \
  research/dart_cfd_pilot/tests/test_vortex_shock_ridge_aware.py

set +e
"${PYTHON_BIN}" "${RUNNER}" \
  --config "${HOLDOUT_CONFIG}" \
  --sra-config "${SPATIAL_CONFIG}" \
  --temporal-config "${TEMPORAL_CONFIG}" \
  --simulation-dir "${SIMULATION_ROOT}/square_holdout" \
  --output-dir "${OUTPUT_DIR}/square_holdout"
HOLDOUT_RC=$?

"${PYTHON_BIN}" "${RUNNER}" \
  --config "${SENSITIVITY_CONFIG}" \
  --sra-config "${SPATIAL_CONFIG}" \
  --temporal-config "${TEMPORAL_CONFIG}" \
  --simulation-dir "${SIMULATION_ROOT}/blockage_sensitivity" \
  --output-dir "${OUTPUT_DIR}/blockage_sensitivity"
SENSITIVITY_RC=$?
set -e
readonly HOLDOUT_RC SENSITIVITY_RC

for RC in "${HOLDOUT_RC}" "${SENSITIVITY_RC}"; do
  if [[ "${RC}" -ne 0 && "${RC}" -ne 5 ]]; then
    echo "ERROR: cross-geometry run ended with technical rc=${RC}" >&2
    exit "${RC}"
  fi
done

{
  echo "job_id=${JOB_ID}"
  echo "git_commit=$(git rev-parse HEAD)"
  echo "detector_freeze_commit=${DETECTOR_FREEZE_COMMIT}"
  echo "holdout_protocol_freeze_commit=${HOLDOUT_PROTOCOL_FREEZE_COMMIT}"
  echo "host=$(hostname)"
  echo "allocated_cpus=${SLURM_CPUS_PER_TASK:-unknown}"
  echo "square_holdout_scientific_rc=${HOLDOUT_RC}"
  echo "blockage_sensitivity_scientific_rc=${SENSITIVITY_RC}"
  "${PYTHON_BIN}" --version
  cc --version | head -n 1
} > "${OUTPUT_DIR}/cross_geometry_environment.txt"

cp \
  "${PROJECT_ROOT}/research/dart_cfd_pilot/CROSS_GEOMETRY_VORTEX_VALIDATION.md" \
  "${OUTPUT_DIR}/CROSS_GEOMETRY_VORTEX_VALIDATION.md"
tar --no-same-owner -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"

echo "VORTEX_CROSS_GEOMETRY_HOLDOUT_RC=${HOLDOUT_RC}"
echo "VORTEX_CROSS_GEOMETRY_SENSITIVITY_RC=${SENSITIVITY_RC}"
echo "VORTEX_CROSS_GEOMETRY_STATUS=completed"
echo "VORTEX_CROSS_GEOMETRY_ARCHIVE=${ARCHIVE}"
echo "VORTEX_CROSS_GEOMETRY_CHECKSUM=${CHECKSUM}"
exit 0
