#!/usr/bin/env bash
#SBATCH --job-name=mfc-dia
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=8
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --time=12:00:00
#SBATCH --output=mfc-diamond-%j.out
#SBATCH --error=mfc-diamond-%j.err

set -euo pipefail

: "${MFC_REPO_DIR:?MFC_REPO_DIR was not exported by unity_submit_mfc.sh}"
: "${MFC_IMAGE:?MFC_IMAGE was not exported by unity_submit_mfc.sh}"
: "${MFC_ALPHA:?MFC_ALPHA was not exported by unity_submit_mfc.sh}"
: "${MFC_MODE:?MFC_MODE was not exported by unity_submit_mfc.sh}"
: "${MFC_GRID:?MFC_GRID was not exported by unity_submit_mfc.sh}"
MFC_STEPS="${MFC_STEPS:-}"

module load apptainer/latest

case_path="${MFC_REPO_DIR}/mfc_crosscheck/case.py"
stl_path="${MFC_REPO_DIR}/mfc_crosscheck/Diamond_Airfoil.stl"
steps_tag="${MFC_STEPS:+_steps${MFC_STEPS}}"
tag="alpha${MFC_ALPHA}_${MFC_MODE}_${MFC_GRID}${steps_tag}"
archive_root="${MFC_REPO_DIR}/mfc_runs/${tag}"

[[ -f "$case_path" ]] || { echo "ERROR: missing $case_path" >&2; exit 2; }
[[ -f "$stl_path" ]] || { echo "ERROR: missing $stl_path" >&2; exit 2; }
[[ -s "$MFC_IMAGE" ]] || { echo "ERROR: missing MFC image $MFC_IMAGE" >&2; exit 2; }
mkdir -p "$archive_root"

echo "JOB_ID=${SLURM_JOB_ID}"
echo "HOST=$(hostname)"
echo "MFC_ALPHA=${MFC_ALPHA}"
echo "MFC_MODE=${MFC_MODE}"
echo "MFC_GRID=${MFC_GRID}"
echo "MFC_STEPS=${MFC_STEPS:-preset-default}"
echo "MFC_IMAGE=${MFC_IMAGE}"
echo "MFC_IMAGE_SHA256=$(sha256sum "$MFC_IMAGE" | awk '{print $1}')"
echo "ARCHIVE_ROOT=${archive_root}"

steps_arg=""
if [[ -n "$MFC_STEPS" ]]; then
    steps_arg="--steps ${MFC_STEPS}"
fi

apptainer exec --writable-tmpfs \
    --bind "${MFC_REPO_DIR}:/work" \
    "$MFC_IMAGE" \
    bash -lc "
        set -euo pipefail
        export UV_CACHE_DIR=/tmp/mfc-uv-cache
        export XDG_CACHE_HOME=/tmp/mfc-xdg-cache
        cd /opt/MFC
        ./mfc.sh run /work/mfc_crosscheck/case.py \
            -n ${SLURM_NTASKS} \
            --archive /work/mfc_runs/${tag} -- \
            --alpha ${MFC_ALPHA} \
            --mode ${MFC_MODE} \
            --grid ${MFC_GRID} ${steps_arg}
    "

echo "MFC_RUN_COMPLETE=1"
echo "ARCHIVE_ROOT=${archive_root}"
