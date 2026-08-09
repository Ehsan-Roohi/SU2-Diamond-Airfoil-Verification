#!/usr/bin/env bash
#SBATCH --job-name=su2-u30
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=96G
#SBATCH --time=2-00:00:00
#SBATCH --output=su2-unsteady-%j.out
#SBATCH --error=su2-unsteady-%j.err

set -euo pipefail

: "${SU2_REPO_DIR:?SU2_REPO_DIR was not exported by the submit wrapper}"

case_dir="${SU2_REPO_DIR}/su2_unsteady_euler"
archive_root="${SU2_REPO_DIR}/su2_runs/alpha30_euler_wakefine_unsteady"
run_dir="${archive_root}/job_${SLURM_JOB_ID}"
mkdir -p "${run_dir}/mesh"

cp "${case_dir}/startup_alpha30.cfg" "${run_dir}/startup_alpha30.cfg"
cp "${case_dir}/unsteady_alpha30.cfg" "${run_dir}/unsteady_alpha30.cfg"

echo "JOB_ID=${SLURM_JOB_ID}"
echo "HOST=$(hostname)"
echo "RUN_DIR=${run_dir}"
echo "REPOSITORY_COMMIT=$(git -C "${SU2_REPO_DIR}" rev-parse HEAD 2>/dev/null || echo UNKNOWN)"
echo "SLURM_CPUS_PER_TASK=${SLURM_CPUS_PER_TASK}"

python "${case_dir}/generate_wakefine_ogrid.py" \
    --output "${run_dir}/mesh/diamond_euler_sharp_wakefine_1440x721.su2" \
    2>&1 | tee "${run_dir}/mesh_generation.log"

if [[ -n "${SU2_CFD_BIN:-}" && -x "${SU2_CFD_BIN}" ]]; then
    su2_cfd="${SU2_CFD_BIN}"
elif command -v SU2_CFD >/dev/null 2>&1; then
    su2_cfd="$(command -v SU2_CFD)"
else
    for module_name in su2/8.5.0 SU2/8.5.0 su2 SU2; do
        module load "${module_name}" >/dev/null 2>&1 || true
        if command -v SU2_CFD >/dev/null 2>&1; then
            break
        fi
    done
    su2_cfd="$(command -v SU2_CFD || true)"
fi

if [[ -z "${su2_cfd}" || ! -x "${su2_cfd}" ]]; then
    echo "ERROR: SU2_CFD was not found. Export SU2_CFD_BIN=/absolute/path/to/SU2_CFD before submission." >&2
    exit 2
fi

echo "SU2_CFD=${su2_cfd}"
"${su2_cfd}" --help > "${run_dir}/su2_help.txt" 2>&1 || true
head -n 12 "${run_dir}/su2_help.txt" || true

export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK}"
export OMP_PROC_BIND=spread
export OMP_PLACES=cores

cd "${run_dir}"

echo "===== STEADY FIRST-ORDER INITIALIZATION ====="
"${su2_cfd}" -t "${SLURM_CPUS_PER_TASK}" startup_alpha30.cfg \
    2>&1 | tee solver_startup.log

[[ -s restart_startup.csv ]] || {
    echo "ERROR: startup did not create restart_startup.csv" >&2
    exit 3
}

echo "===== SECOND-ORDER DUAL-TIME EULER ====="
"${su2_cfd}" -t "${SLURM_CPUS_PER_TASK}" unsteady_alpha30.cfg \
    2>&1 | tee solver_unsteady.log

[[ -s history_unsteady.csv ]] || {
    echo "ERROR: unsteady stage did not create history_unsteady.csv" >&2
    exit 4
}

snapshot_count=$(find . -maxdepth 1 -type f -name 'flow_unsteady_*.vtu' | wc -l)
restart_count=$(find . -maxdepth 1 -type f -name 'restart_unsteady_*.csv' | wc -l)
echo "FLOW_SNAPSHOT_COUNT=${snapshot_count}"
echo "RESTART_SNAPSHOT_COUNT=${restart_count}"
echo "SU2_UNSTEADY_RUN_COMPLETE=1"
echo "RUN_DIR=${run_dir}"
