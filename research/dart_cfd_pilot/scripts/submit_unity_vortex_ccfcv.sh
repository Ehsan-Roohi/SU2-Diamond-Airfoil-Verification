#!/usr/bin/env bash
#SBATCH --job-name=vortex-ccfcv
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=32
#SBATCH --cpus-per-task=1
#SBATCH --mem=96G
#SBATCH --time=24:00:00
#SBATCH --output=logs/vortex-ccfcv-%j.out
#SBATCH --error=logs/vortex-ccfcv-%j.err
#SBATCH --mail-type=END,FAIL,TIME_LIMIT_80

set -Eeuo pipefail
umask 077

readonly SOURCE_COMMIT="6f71c45d1223dab62dc8f65b1f05dc369ab5932e"
readonly SOURCE_BRANCH="agent/mfc-a40-iles-final-case"
readonly SOURCE_DIR="mfc_iles_a40/final_w5unmapped_hllc_dt1"
readonly MFC_COMMIT="0c9a1d434410175ac483b8d71646455444e3b7eb"
readonly ALPHA_DEG="${VORTEX_CCFCV_ALPHA_DEG:-30}"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${VORTEX_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${VORTEX_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly MFC_ROOT="${VORTEX_CCFCV_MFC_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
readonly CASE_DIR="${VORTEX_CCFCV_MFC_CASE_DIR:-${WORK_ROOT}/ccfcv-alpha30-raw}"
readonly SOURCE_BASELINE="${VORTEX_CCFCV_SOURCE_BASELINE:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63809195/stage14_report.json}"
readonly TEST_PYTHON="${WORK_ROOT}/conda/dart-sam3-py311/bin/python"
readonly MFC_PYTHON="${MFC_ROOT}/build/venv/bin/python3"
readonly RUN_ID="${SLURM_JOB_ID:-ccfcv-manual}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${RUN_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_CCFCV_ALPHA30_${RUN_ID}_COMPLETE.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"

[[ "${ALPHA_DEG}" == "30" || "${ALPHA_DEG}" == "30.0" ]] || {
    echo "ERROR: the preregistered run requires alpha=30 degrees" >&2
    exit 2
}
[[ -x "${MFC_PYTHON}" ]] || { echo "ERROR: pinned MFC Python missing" >&2; exit 3; }
[[ -x "${TEST_PYTHON}" ]] || { echo "ERROR: test environment missing" >&2; exit 4; }
[[ -f "${SOURCE_BASELINE}" ]] || { echo "ERROR: frozen alpha-40 baseline report missing" >&2; exit 5; }
[[ "$(git -C "${MFC_ROOT}" rev-parse HEAD)" == "${MFC_COMMIT}" ]] || {
    echo "ERROR: MFC commit does not match ${MFC_COMMIT}" >&2
    exit 6
}

mkdir -p "${CASE_DIR}" "${CASE_DIR}/restart_data" "${OUTPUT_DIR}" "${PROJECT_ROOT}/logs"
git -C "${PROJECT_ROOT}" fetch origin "${SOURCE_BRANCH}"
[[ "$(git -C "${PROJECT_ROOT}" rev-parse FETCH_HEAD)" == "${SOURCE_COMMIT}" ]] || {
    echo "ERROR: pinned historical source moved" >&2
    exit 7
}
git -C "${PROJECT_ROOT}" show "${SOURCE_COMMIT}:${SOURCE_DIR}/case.py" > "${CASE_DIR}/case_source_alpha40.py.part"
"${TEST_PYTHON}" "${PROJECT_ROOT}/research/dart_cfd_pilot/scripts/materialize_mfc_cross_case.py" \
    --source "${CASE_DIR}/case_source_alpha40.py.part" \
    --output "${CASE_DIR}/case.py" --alpha-deg "${ALPHA_DEG}"
mv "${CASE_DIR}/case_source_alpha40.py.part" "${CASE_DIR}/case_source_alpha40.py"
git -C "${PROJECT_ROOT}" show "${SOURCE_COMMIT}:${SOURCE_DIR}/Diamond_Airfoil_2D_MFC.stl" \
    > "${CASE_DIR}/Diamond_Airfoil_2D_MFC.stl.part"
mv "${CASE_DIR}/Diamond_Airfoil_2D_MFC.stl.part" "${CASE_DIR}/Diamond_Airfoil_2D_MFC.stl"

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
case_args=(--mode initial --grid f270 --start-time 0 --final-time 3.0 --save-dt 0.05 --dt-factor 1 --format binary)
if [[ ! -f "${CASE_DIR}/RUN_OK_CCFCV_RAW_FIELDS.txt" ]]; then
    if find "${CASE_DIR}/restart_data" -maxdepth 1 -type f -name 'lustre_[0-9]*.dat' -print -quit | grep -q .; then
        echo "ERROR: partial CC-FCV run exists without completion marker: ${CASE_DIR}" >&2
        exit 8
    fi
    cd "${MFC_ROOT}"
    ./mfc.sh validate "${CASE_DIR}/case.py" -- "${case_args[@]}" 2>&1 | tee "${CASE_DIR}/validate.log"
    ./mfc.sh run "${CASE_DIR}/case.py" -n "${SLURM_NTASKS}" -j "${SLURM_NTASKS}" \
        --mpi --no-gpu --binary mpirun --no-build -t pre_process -- "${case_args[@]}" \
        2>&1 | tee "${CASE_DIR}/pre_process.log"
    ./mfc.sh run "${CASE_DIR}/case.py" -n "${SLURM_NTASKS}" -j "${SLURM_NTASKS}" \
        --mpi --no-gpu --binary mpirun --no-build -t simulation -- "${case_args[@]}" \
        2>&1 | tee "${CASE_DIR}/simulation.log"
    ./mfc.sh run "${CASE_DIR}/case.py" -n "${SLURM_NTASKS}" -j "${SLURM_NTASKS}" \
        --mpi --no-gpu --binary mpirun --no-build -t post_process -- "${case_args[@]}" \
        2>&1 | tee "${CASE_DIR}/post_process.log"
    {
        echo "status=PASS"
        echo "alpha_deg=${ALPHA_DEG}"
        echo "mfc_commit=${MFC_COMMIT}"
        echo "case_source_commit=${SOURCE_COMMIT}"
        echo "final_step=16200"
        echo "case_sha256=$(sha256sum "${CASE_DIR}/case.py" | awk '{print $1}')"
    } > "${CASE_DIR}/RUN_OK_CCFCV_RAW_FIELDS.txt"
fi

cd "${PROJECT_ROOT}"
"${TEST_PYTHON}" -m pytest -q research/dart_cfd_pilot/tests
PYTHONPATH="${MFC_ROOT}/toolchain${PYTHONPATH:+:${PYTHONPATH}}" \
    "${MFC_PYTHON}" research/dart_cfd_pilot/scripts/run_vortex_ccfcv.py \
    --case-dir "${CASE_DIR}" --mfc-root "${MFC_ROOT}" \
    --source-baseline-report "${SOURCE_BASELINE}" --output-dir "${OUTPUT_DIR}"

{
    echo "slurm_job_id=${SLURM_JOB_ID:-manual}"
    echo "host=${HOSTNAME}"
    echo "project_commit=$(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
    echo "mfc_commit=${MFC_COMMIT}"
    echo "source_case_commit=${SOURCE_COMMIT}"
    echo "alpha_deg=${ALPHA_DEG}"
    echo "raw_case_dir=${CASE_DIR}"
    echo "frozen_baseline_report=${SOURCE_BASELINE}"
} > "${OUTPUT_DIR}/ccfcv_environment.txt"

tar --no-same-owner -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"
echo "CCFCV_RC=0"
echo "CCFCV_OUTPUT_DIR=${OUTPUT_DIR}"
echo "CCFCV_ARCHIVE=${ARCHIVE}"
echo "CCFCV_CHECKSUM=${CHECKSUM}"
