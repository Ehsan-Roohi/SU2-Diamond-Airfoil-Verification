#!/usr/bin/env bash
#SBATCH --job-name=dart-cfd
#SBATCH --account=pi_roohie_umass_edu
#SBATCH --partition=gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --gres=gpu:a100:1
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=logs/dart-cfd-%j.out
#SBATCH --error=logs/dart-cfd-%j.err
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
readonly CHECKPOINT_DIR="${WORK_ROOT}/checkpoints"
readonly CHECKPOINT="${SAM3_CHECKPOINT:-${CHECKPOINT_DIR}/sam3.pt}"
readonly RUN_ID="${SLURM_JOB_ID:-manual}"
readonly OUTPUT_REL="results/${RUN_ID}"
readonly OUTPUT_DIR="${PROJECT_ROOT}/research/dart_cfd_pilot/${OUTPUT_REL}"
readonly ARCHIVE="${PROJECT_ROOT}/research/dart_cfd_pilot/results/${RUN_ID}.tar.gz"
readonly CHECKSUM="${ARCHIVE}.sha256.txt"

if [[ ! -f "${PROJECT_ROOT}/research/dart_cfd_pilot/dart_cases.json" ]]; then
    echo "ERROR: DART pilot repository not found under PROJECT_ROOT=${PROJECT_ROOT}" >&2
    echo "Submit this script from the repository root or set DART_PROJECT_ROOT explicitly." >&2
    exit 2
fi

mkdir -p "${WORK_ROOT}" "${CHECKPOINT_DIR}" "${OUTPUT_DIR}"
export HF_HOME="${WORK_ROOT}/hf-cache"
export HF_HUB_CACHE="${HF_HOME}/hub"
export TRANSFORMERS_CACHE="${HF_HOME}/transformers"
export TORCH_HOME="${WORK_ROOT}/torch-cache"
export PIP_CACHE_DIR="${WORK_ROOT}/pip-cache"

module purge
module load conda/latest
module load cuda/12.6
source "$(conda info --base)/etc/profile.d/conda.sh"

if [[ ! -x "${ENV_PREFIX}/bin/python" ]]; then
    conda create --prefix "${ENV_PREFIX}" python=3.11 -y
fi
conda activate "${ENV_PREFIX}"

if [[ ! -d "${DART_REPO}/.git" ]]; then
    git clone https://github.com/mkturkcan/DART.git "${DART_REPO}"
fi
git -C "${DART_REPO}" fetch origin "${DART_COMMIT}"
git -C "${DART_REPO}" checkout --detach "${DART_COMMIT}"

readonly INSTALL_STAMP="${ENV_PREFIX}/.dart-${DART_COMMIT}.ready"
if [[ ! -f "${INSTALL_STAMP}" ]]; then
    python -m pip install --upgrade pip "setuptools==${SETUPTOOLS_VERSION}" wheel
    python -m pip install torch==2.7.0 torchvision==0.22.0 \
        --index-url https://download.pytorch.org/whl/cu126
    python -m pip install -e "${DART_REPO}"
    python -m pip install pillow pytest huggingface_hub
    touch "${INSTALL_STAMP}"
fi

# DART's pinned SAM3 code imports pkg_resources, removed in setuptools 82.
# Repair environments created by older versions of this submit script even when
# their install stamp already exists.
if ! python -c 'import pkg_resources' >/dev/null 2>&1; then
    python -m pip install "setuptools==${SETUPTOOLS_VERSION}"
fi
python -W ignore::UserWarning -c 'import pkg_resources'

if [[ ! -s "${CHECKPOINT}" ]]; then
    if [[ -z "${HF_TOKEN:-}" ]]; then
        echo "ERROR: sam3.pt is absent and HF_TOKEN was not exported." >&2
        echo "Request access to https://huggingface.co/facebook/sam3, export HF_TOKEN, and resubmit." >&2
        exit 42
    fi
    python - "${CHECKPOINT_DIR}" <<'PY'
import os
import sys
from huggingface_hub import hf_hub_download

hf_hub_download(
    repo_id="facebook/sam3",
    filename="sam3.pt",
    local_dir=sys.argv[1],
    token=os.environ["HF_TOKEN"],
)
PY
fi
unset HF_TOKEN HUGGING_FACE_HUB_TOKEN || true

cd "${PROJECT_ROOT}"
python - <<'PY'
import torch

if not torch.cuda.is_available():
    raise SystemExit("CUDA preflight failed: torch.cuda.is_available() is false")
print(f"torch={torch.__version__}")
print(f"cuda_runtime={torch.version.cuda}")
print(f"gpu={torch.cuda.get_device_name(0)}")
print(f"capability={torch.cuda.get_device_capability(0)}")
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
} > "${OUTPUT_DIR}/unity_environment.txt"

set +e
python research/dart_cfd_pilot/scripts/run_dart_pilot.py \
    --dart-repo "${DART_REPO}" \
    --checkpoint "${CHECKPOINT}" \
    --device cuda \
    --imgsz 1008 \
    --confidence 0.15 \
    --output-dir "${OUTPUT_REL}"
run_rc=$?
set -e

tar -C "${OUTPUT_DIR}" -czf "${ARCHIVE}" .
sha256sum "${ARCHIVE}" > "${CHECKSUM}"
echo "DART_RUN_RC=${run_rc}"
echo "DART_OUTPUT_DIR=${OUTPUT_DIR}"
echo "DART_ARCHIVE=${ARCHIVE}"
echo "DART_CHECKSUM=${CHECKSUM}"
exit "${run_rc}"
