#!/usr/bin/env bash
#SBATCH --job-name=vortex-xsv
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=logs/vortex-xsv-%j.out
#SBATCH --error=logs/vortex-xsv-%j.err
#SBATCH --mail-type=END,FAIL,TIME_LIMIT_80

set -Eeuo pipefail
umask 077

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${VORTEX_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${VORTEX_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly MFC_ROOT="${VORTEX_XSV_MFC_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
readonly MFC_CASE="${VORTEX_XSV_MFC_CASE:-${WORK_ROOT}/stage5-mfc-raw}"
readonly MFC_REFERENCE="${VORTEX_XSV_MFC_REFERENCE:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63792493/stage8_catalogue.csv}"
readonly SU2_CHECKPOINT="${VORTEX_XSV_SU2_CHECKPOINT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data/checkpoints/urans_alpha40/medium_halfdt/URANS_alpha40_medium_halfdt_checkpoint_t012000.zip}"
readonly PYTHON_BIN="${VORTEX_XSV_PYTHON:-${WORK_ROOT}/conda/dart-sam3-py311/bin/python}"
readonly REQUIREMENTS="${PROJECT_ROOT}/research/dart_cfd_pilot/requirements-shock-ridge-aware.txt"
readonly CONFIG="${PROJECT_ROOT}/research/dart_cfd_pilot/vortex_mfc_su2_cross_solver_audit.json"
readonly RUNNER="${PROJECT_ROOT}/research/dart_cfd_pilot/scripts/run_vortex_mfc_su2_cross_solver_audit.py"
readonly JOB_ID="${SLURM_JOB_ID:-manual}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${JOB_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_MFC_SU2_CROSS_SOLVER_${JOB_ID}_COMPLETE.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"
readonly PROTOCOL_COMMIT="b6a782f772cb1da64096f2f339532d2ed296ad6c"
readonly PROTOCOL_REF="provenance/tsa-sra-cmcd-v2-freeze-record"

for required in "${CONFIG}" "${RUNNER}" "${REQUIREMENTS}" "${SU2_CHECKPOINT}" "${MFC_REFERENCE}"; do
  [[ -s "${required}" ]] || { echo "ERROR: required input missing or empty: ${required}" >&2; exit 2; }
done
[[ -x "${PYTHON_BIN}" ]] || { echo "ERROR: analysis Python is not executable: ${PYTHON_BIN}" >&2; exit 2; }
[[ -x "${MFC_ROOT}/build/venv/bin/python3" ]] || { echo "ERROR: pinned MFC build is missing: ${MFC_ROOT}" >&2; exit 2; }
grep -qx 'status=PASS' "${MFC_CASE}/RUN_OK_RAW_FIELDS.txt" || {
  echo "ERROR: completed MFC raw-field marker is invalid: ${MFC_CASE}" >&2
  exit 2
}

mkdir -p "${PROJECT_ROOT}/logs" "${OUTPUT_DIR}" "${WORK_ROOT}/matplotlib" "${WORK_ROOT}/pip-cache"
export PYTHONNOUSERSITE=1
export MPLCONFIGDIR="${WORK_ROOT}/matplotlib"
export PIP_CACHE_DIR="${WORK_ROOT}/pip-cache"
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
export PYTHONPATH="${MFC_ROOT}/toolchain${PYTHONPATH:+:${PYTHONPATH}}"

if ! "${PYTHON_BIN}" -c 'import matplotlib.pyplot, numpy, pyparsing, pytest, scipy; from mfc.viz.reader import assemble'; then
  "${PYTHON_BIN}" -m pip install --disable-pip-version-check --no-input \
    --upgrade --requirement "${REQUIREMENTS}"
fi

cd "${PROJECT_ROOT}"
git fetch origin "${PROTOCOL_REF}"
git cat-file -e "${PROTOCOL_COMMIT}^{commit}"
"${PYTHON_BIN}" -m pytest -q \
  research/dart_cfd_pilot/tests/test_vortex_mfc_su2_cross_solver.py \
  research/dart_cfd_pilot/tests/test_vortex_temporal_cylinder_validation.py \
  research/dart_cfd_pilot/tests/test_vortex_analytic_positive_control.py \
  research/dart_cfd_pilot/tests/test_vortex_shock_ridge_aware.py

"${PYTHON_BIN}" "${RUNNER}" \
  --solver mfc --input "${MFC_CASE}" --mfc-root "${MFC_ROOT}" \
  --reference-catalogue "${MFC_REFERENCE}" \
  --config "${CONFIG}" --output-dir "${OUTPUT_DIR}"
"${PYTHON_BIN}" "${RUNNER}" \
  --solver su2 --input "${SU2_CHECKPOINT}" \
  --config "${CONFIG}" --output-dir "${OUTPUT_DIR}"

{
  echo "job_id=${JOB_ID}"
  echo "method=TSA-SRA-CMCD-v2"
  echo "role=retrospective_cross_solver_diagnostic_not_independent"
  echo "project_commit=$(git rev-parse HEAD)"
  echo "protocol_commit=${PROTOCOL_COMMIT}"
  echo "mfc_case=${MFC_CASE}"
  echo "mfc_reference=${MFC_REFERENCE}"
  echo "mfc_reference_sha256=$(sha256sum "${MFC_REFERENCE}" | awk '{print $1}')"
  echo "mfc_commit=$(git -C "${MFC_ROOT}" rev-parse HEAD)"
  echo "su2_checkpoint=${SU2_CHECKPOINT}"
  echo "su2_checkpoint_sha256=$(sha256sum "${SU2_CHECKPOINT}" | awk '{print $1}')"
  "${PYTHON_BIN}" --version
} > "${OUTPUT_DIR}/cross_solver_environment.txt"

tar --no-same-owner -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"
echo "VORTEX_CROSS_SOLVER_STATUS=completed"
echo "VORTEX_CROSS_SOLVER_OUTPUT_DIR=${OUTPUT_DIR}"
echo "VORTEX_CROSS_SOLVER_ARCHIVE=${ARCHIVE}"
echo "VORTEX_CROSS_SOLVER_CHECKSUM=${CHECKSUM}"
