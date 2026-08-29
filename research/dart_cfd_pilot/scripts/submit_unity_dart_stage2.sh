#!/usr/bin/env bash
#SBATCH --job-name=dart-cfd-s2
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gres=gpu:a100:1
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=logs/dart-cfd-s2-%j.out
#SBATCH --error=logs/dart-cfd-s2-%j.err
#SBATCH --mail-type=END,FAIL,TIME_LIMIT_80

set -Eeuo pipefail
umask 077

readonly DART_COMMIT="b4f954319ad4c26ab1372d130719eb2f4ddd4ea6"
readonly SETUPTOOLS_VERSION="81.0.0"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly PROJECT_ROOT="${DART_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-${SOURCE_PROJECT_ROOT}}}"
readonly WORK_ROOT="${DART_WORK_ROOT:-/project/pi_roohie_umass_edu/DART_CFD_PILOT}"
readonly DART_REPO="${WORK_ROOT}/DART"
readonly ENV_PREFIX="${WORK_ROOT}/conda/dart-sam3-py311"
readonly CHECKPOINT="${SAM3_CHECKPOINT:-${WORK_ROOT}/checkpoints/sam3.pt}"
readonly RUN_ID="${SLURM_JOB_ID:-stage2-manual}"
readonly OUTPUT_REL="results/${RUN_ID}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/${OUTPUT_REL}"
readonly ARCHIVE="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${RUN_ID}.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"

if [[ ! -f "${PROJECT_ROOT}/research/dart_cfd_pilot/dart_stage2.json" ]]; then
    echo "ERROR: stage-2 configuration not found under PROJECT_ROOT=${PROJECT_ROOT}" >&2
    exit 2
fi
if [[ ! -x "${ENV_PREFIX}/bin/python" || ! -d "${DART_REPO}/.git" ]]; then
    echo "ERROR: reusable DART environment is absent under ${WORK_ROOT}." >&2
    echo "Run submit_unity_dart_pilot.sh once to bootstrap the pinned environment." >&2
    exit 3
fi
if [[ ! -s "${CHECKPOINT}" ]]; then
    echo "ERROR: SAM3 checkpoint is absent: ${CHECKPOINT}" >&2
    exit 4
fi

mkdir -p "${OUTPUT_DIR}" "${PROJECT_ROOT}/logs"
export HF_HOME="${WORK_ROOT}/hf-cache"
export HF_HUB_CACHE="${HF_HOME}/hub"
export TRANSFORMERS_CACHE="${HF_HOME}/transformers"
export TORCH_HOME="${WORK_ROOT}/torch-cache"
export PIP_CACHE_DIR="${WORK_ROOT}/pip-cache"

module purge
module load conda/latest
module load cuda/12.6
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${ENV_PREFIX}"

git -C "${DART_REPO}" fetch origin "${DART_COMMIT}"
git -C "${DART_REPO}" checkout --detach "${DART_COMMIT}"

if ! python -c 'import pkg_resources' >/dev/null 2>&1; then
    python -m pip install "setuptools==${SETUPTOOLS_VERSION}"
fi

cd "${PROJECT_ROOT}"
python - <<'PY'
import torch

if not torch.cuda.is_available():
    raise SystemExit("CUDA preflight failed: torch.cuda.is_available() is false")
print(f"torch={torch.__version__}")
print(f"cuda_runtime={torch.version.cuda}")
print(f"gpu={torch.cuda.get_device_name(0)}")
PY

python -m pytest -q research/dart_cfd_pilot/tests

{
    echo "slurm_job_id=${SLURM_JOB_ID:-manual}"
    echo "host=${HOSTNAME}"
    echo "project_commit=$(git rev-parse HEAD)"
    echo "dart_commit=$(git -C "${DART_REPO}" rev-parse HEAD)"
    echo "checkpoint_sha256=$(sha256sum "${CHECKPOINT}" | awk '{print $1}')"
    python -c 'import torch; print(f"torch={torch.__version__}\ncuda_runtime={torch.version.cuda}\ngpu={torch.cuda.get_device_name(0)}")'
    nvidia-smi --query-gpu=name,uuid,driver_version,memory.total --format=csv,noheader
} > "${OUTPUT_DIR}/stage2_environment.txt"

set +e
python research/dart_cfd_pilot/scripts/run_dart_stage2.py \
    --dart-repo "${DART_REPO}" \
    --checkpoint "${CHECKPOINT}" \
    --device cuda \
    --imgsz 1008 \
    --output-dir "${OUTPUT_REL}"
run_rc=$?
set -e

tar -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"
echo "DART_STAGE2_RC=${run_rc}"
echo "DART_STAGE2_OUTPUT_DIR=${OUTPUT_DIR}"
echo "DART_STAGE2_ARCHIVE=${ARCHIVE}"
echo "DART_STAGE2_CHECKSUM=${CHECKSUM}"
exit "${run_rc}"
