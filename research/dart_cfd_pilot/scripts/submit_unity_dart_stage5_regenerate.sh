#!/usr/bin/env bash
#SBATCH --job-name=dart-cfd-s5
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=32
#SBATCH --cpus-per-task=1
#SBATCH --mem=96G
#SBATCH --time=24:00:00
#SBATCH --output=logs/dart-cfd-s5-%j.out
#SBATCH --error=logs/dart-cfd-s5-%j.err
#SBATCH --mail-type=END,FAIL,TIME_LIMIT_80

set -Eeuo pipefail
umask 077

readonly MFC_CASE_COMMIT="6f71c45d1223dab62dc8f65b1f05dc369ab5932e"
readonly MFC_BRANCH="agent/mfc-a40-iles-final-case"
readonly MFC_SOURCE_DIR="mfc_iles_a40/final_w5unmapped_hllc_dt1"
readonly MFC_COMMIT="0c9a1d434410175ac483b8d71646455444e3b7eb"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${DART_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${DART_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly MFC_ROOT="${DART_STAGE5_MFC_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
readonly CASE_DIR="${DART_STAGE5_MFC_CASE_DIR:-${WORK_ROOT}/stage5-mfc-raw}"
readonly STAGE3_DIR="${DART_STAGE5_STAGE3_DIR:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63761044}"
readonly ENV_PREFIX="${WORK_ROOT}/conda/dart-sam3-py311"
readonly RUN_ID="${SLURM_JOB_ID:-stage5-manual}"
readonly OUTPUT_REL="results/${RUN_ID}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/${OUTPUT_REL}"
readonly ARCHIVE="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${RUN_ID}.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"

[[ -x "${MFC_ROOT}/build/venv/bin/python3" ]] || {
    echo "ERROR: pinned MFC environment not found: ${MFC_ROOT}" >&2
    exit 2
}
[[ -x "${ENV_PREFIX}/bin/python" ]] || {
    echo "ERROR: DART Python environment not found: ${ENV_PREFIX}" >&2
    exit 3
}
[[ -f "${STAGE3_DIR}/stage3_report.json" && -f "${STAGE3_DIR}/stage3_tracks.csv" ]] || {
    echo "ERROR: Stage-3 source result not found: ${STAGE3_DIR}" >&2
    exit 4
}
mfc_head="$(git -C "${MFC_ROOT}" rev-parse HEAD)"
[[ "${mfc_head}" == "${MFC_COMMIT}" ]] || {
    echo "ERROR: MFC HEAD ${mfc_head} does not match pinned ${MFC_COMMIT}" >&2
    exit 5
}

mkdir -p "${CASE_DIR}" "${CASE_DIR}/restart_data" "${OUTPUT_DIR}" "${PROJECT_ROOT}/logs"
git -C "${PROJECT_ROOT}" fetch origin "${MFC_BRANCH}"
[[ "$(git -C "${PROJECT_ROOT}" rev-parse FETCH_HEAD)" == "${MFC_CASE_COMMIT}" ]] || {
    echo "ERROR: historical MFC branch moved from ${MFC_CASE_COMMIT}" >&2
    exit 6
}
for source in case.py Diamond_Airfoil_2D_MFC.stl; do
    temporary="${CASE_DIR}/${source}.part"
    git -C "${PROJECT_ROOT}" show "${MFC_CASE_COMMIT}:${MFC_SOURCE_DIR}/${source}" > "${temporary}"
    mv "${temporary}" "${CASE_DIR}/${source}"
done

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
readonly MFC_PYTHON="${MFC_ROOT}/build/venv/bin/python3"
case_args=(--mode initial --grid f270 --start-time 0 --final-time 3.0 --save-dt 0.05 --dt-factor 1 --format binary)

if [[ ! -f "${CASE_DIR}/RUN_OK_RAW_FIELDS.txt" ]]; then
    if find "${CASE_DIR}/restart_data" -maxdepth 1 -type f -name 'lustre_[0-9]*.dat' -print -quit | grep -q .; then
        echo "ERROR: partial Stage-5 raw run exists without a completion marker: ${CASE_DIR}" >&2
        echo "Set DART_STAGE5_MFC_CASE_DIR to a fresh directory; no partial data were overwritten." >&2
        exit 7
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
    printf 'status=PASS\nmfc_commit=%s\ncase_source_commit=%s\nfinal_step=16200\n' \
        "${MFC_COMMIT}" "${MFC_CASE_COMMIT}" > "${CASE_DIR}/RUN_OK_RAW_FIELDS.txt"
fi

cd "${PROJECT_ROOT}"
"${ENV_PREFIX}/bin/python" -m pytest -q research/dart_cfd_pilot/tests
PYTHONPATH="${MFC_ROOT}/toolchain${PYTHONPATH:+:${PYTHONPATH}}" \
    "${MFC_PYTHON}" research/dart_cfd_pilot/scripts/run_dart_stage5_raw_reference.py \
    --case-dir "${CASE_DIR}" --mfc-root "${MFC_ROOT}" --output-dir "${OUTPUT_REL}"

"${ENV_PREFIX}/bin/python" research/dart_cfd_pilot/scripts/run_dart_stage4_validation.py \
    --stage3-dir "${STAGE3_DIR}" \
    --reference-csv "${OUTPUT_DIR}/stage5_reference.csv" \
    --output-dir "${OUTPUT_REL}"

{
    echo "slurm_job_id=${SLURM_JOB_ID:-manual}"
    echo "host=${HOSTNAME}"
    echo "project_commit=$(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
    echo "mfc_commit=${mfc_head}"
    echo "mfc_case_source_commit=${MFC_CASE_COMMIT}"
    echo "raw_case_dir=${CASE_DIR}"
    echo "stage3_directory=${STAGE3_DIR}"
} > "${OUTPUT_DIR}/stage5_environment.txt"

tar -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"
echo "DART_STAGE5_RC=0"
echo "DART_STAGE5_OUTPUT_DIR=${OUTPUT_DIR}"
echo "DART_STAGE5_ARCHIVE=${ARCHIVE}"
echo "DART_STAGE5_CHECKSUM=${CHECKSUM}"
