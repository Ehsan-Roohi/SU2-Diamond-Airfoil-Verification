#!/usr/bin/env bash
#SBATCH --job-name=vortex-ccfcv-r
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=32
#SBATCH --cpus-per-task=1
#SBATCH --mem=96G
#SBATCH --time=02:00:00
#SBATCH --output=logs/vortex-ccfcv-r-%j.out
#SBATCH --error=logs/vortex-ccfcv-r-%j.err
#SBATCH --mail-type=END,FAIL

set -Eeuo pipefail
umask 077

readonly SOURCE_COMMIT="6f71c45d1223dab62dc8f65b1f05dc369ab5932e"
readonly MFC_COMMIT="0c9a1d434410175ac483b8d71646455444e3b7eb"
readonly ALPHA_DEG="30"
readonly EXPECTED_CASE_SHA="6f7f6485890630cd79470ad7d2b4c687798b26296ca941945f85272e6fbf7560"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${VORTEX_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${VORTEX_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly MFC_ROOT="${VORTEX_CCFCV_MFC_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification/third_party/MFC-0c9a1d43-iles-portable-v3}"
readonly CASE_DIR="${VORTEX_CCFCV_MFC_CASE_DIR:-${WORK_ROOT}/ccfcv-alpha30-raw}"
readonly SOURCE_BASELINE="${VORTEX_CCFCV_SOURCE_BASELINE:-${PROJECT_ROOT}/research/dart_cfd_pilot/results/63809195/stage14_report.json}"
readonly TEST_PYTHON="${WORK_ROOT}/conda/dart-sam3-py311/bin/python"
readonly MFC_PYTHON="${MFC_ROOT}/build/venv/bin/python3"
readonly RUN_ID="${SLURM_JOB_ID:-ccfcv-recovery-manual}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${RUN_ID}"
readonly ARCHIVE="${PROJECT_ROOT}/VORTEX_CCFCV_ALPHA30_${RUN_ID}_COMPLETE.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"

[[ -x "${MFC_PYTHON}" ]] || { echo "ERROR: pinned MFC Python missing" >&2; exit 2; }
[[ -x "${TEST_PYTHON}" ]] || { echo "ERROR: test environment missing" >&2; exit 3; }
[[ -f "${SOURCE_BASELINE}" ]] || { echo "ERROR: frozen alpha-40 baseline report missing" >&2; exit 4; }
[[ -f "${CASE_DIR}/case.py" && -f "${CASE_DIR}/simulation.log" ]] || {
    echo "ERROR: recoverable alpha-30 case is missing" >&2
    exit 5
}
[[ "$(git -C "${MFC_ROOT}" rev-parse HEAD)" == "${MFC_COMMIT}" ]] || {
    echo "ERROR: MFC commit does not match ${MFC_COMMIT}" >&2
    exit 6
}
[[ "$(sha256sum "${CASE_DIR}/case.py" | awk '{print $1}')" == "${EXPECTED_CASE_SHA}" ]] || {
    echo "ERROR: alpha-30 case provenance mismatch" >&2
    exit 7
}
grep -q 'Finished MFC:' "${CASE_DIR}/simulation.log" || {
    echo "ERROR: simulation did not reach the MFC completion block" >&2
    exit 8
}
grep -Eq 'Exit Code:[[:space:]]+0' "${CASE_DIR}/simulation.log" || {
    echo "ERROR: simulation completion block has a nonzero exit code" >&2
    exit 8
}
grep -Eq 't_step =[[:space:]]+16199' "${CASE_DIR}/simulation.log" || {
    echo "ERROR: final alpha-30 simulation step was not reached" >&2
    exit 8
}

mkdir -p "${OUTPUT_DIR}" "${PROJECT_ROOT}/logs"
module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=1
case_args=(--mode initial --grid f270 --start-time 0 --final-time 3.0 --save-dt 0.05 --dt-factor 1 --format binary)

verify_binary_sequence() {
    PYTHONPATH="${MFC_ROOT}/toolchain${PYTHONPATH:+:${PYTHONPATH}}" \
        "${MFC_PYTHON}" - "${CASE_DIR}" <<'PY'
import sys
from mfc.viz.reader import discover_timesteps

case_dir = sys.argv[1]
required = set(range(0, 16201, 270))
available = set(discover_timesteps(case_dir, "binary"))
missing = sorted(required - available)
print(f"CCFCV_BINARY_AVAILABLE={len(required) - len(missing)}/{len(required)}")
if missing:
    print("CCFCV_BINARY_MISSING=" + ",".join(map(str, missing)))
    raise SystemExit(1)
PY
}

if verify_binary_sequence; then
    echo "CCFCV_POSTPROCESS_RECOVERY=not_needed"
else
    echo "CCFCV_POSTPROCESS_RECOVERY=starting"
    cd "${MFC_ROOT}"
    ./mfc.sh run "${CASE_DIR}/case.py" -n "${SLURM_NTASKS}" -j "${SLURM_NTASKS}" \
        --mpi --no-gpu --binary mpirun --no-build -t post_process -- "${case_args[@]}" \
        2>&1 | tee "${CASE_DIR}/post_process_recovery_${RUN_ID}.log"
    verify_binary_sequence || {
        echo "ERROR: CC-FCV binary sequence remains incomplete after recovery" >&2
        exit 9
    }
    echo "CCFCV_POSTPROCESS_RECOVERY=completed"
fi

{
    echo "status=PASS"
    echo "alpha_deg=${ALPHA_DEG}"
    echo "mfc_commit=${MFC_COMMIT}"
    echo "case_source_commit=${SOURCE_COMMIT}"
    echo "final_step=16200"
    echo "case_sha256=${EXPECTED_CASE_SHA}"
    echo "recovered_from_job=63811016"
} > "${CASE_DIR}/RUN_OK_CCFCV_RAW_FIELDS.txt"

cd "${PROJECT_ROOT}"
"${TEST_PYTHON}" -m pytest -q research/dart_cfd_pilot/tests
set +e
PYTHONPATH="${MFC_ROOT}/toolchain${PYTHONPATH:+:${PYTHONPATH}}" \
    "${MFC_PYTHON}" research/dart_cfd_pilot/scripts/run_vortex_ccfcv.py \
    --case-dir "${CASE_DIR}" --mfc-root "${MFC_ROOT}" \
    --source-baseline-report "${SOURCE_BASELINE}" --output-dir "${OUTPUT_DIR}"
analysis_rc=$?
set -e
[[ "${analysis_rc}" -eq 0 || "${analysis_rc}" -eq 8 ]] || {
    echo "ERROR: CC-FCV technical execution failed with code ${analysis_rc}" >&2
    exit "${analysis_rc}"
}

{
    echo "slurm_job_id=${SLURM_JOB_ID:-manual}"
    echo "recovered_from_job=63811016"
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
echo "CCFCV_SCIENTIFIC_RC=${analysis_rc}"
echo "CCFCV_OUTPUT_DIR=${OUTPUT_DIR}"
echo "CCFCV_ARCHIVE=${ARCHIVE}"
echo "CCFCV_CHECKSUM=${CHECKSUM}"
