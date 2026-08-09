#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --output=nek-%x-%j.out
#SBATCH --error=nek-%x-%j.err

set -euo pipefail

# Recreate the MPI runtime inside the batch shell.  Do not rely only on the
# login shell's exported PATH: Conda may provide mpicxx without libmpi.so.40.
if ! type module >/dev/null 2>&1; then
    for module_init in \
        /etc/profile.d/modules.sh \
        /etc/profile.d/lmod.sh \
        /usr/share/lmod/lmod/init/bash; do
        if [[ -r "$module_init" ]]; then
            # shellcheck disable=SC1090
            source "$module_init"
            break
        fi
    done
fi
if ! type module >/dev/null 2>&1; then
    echo "Unity module command is unavailable in the Slurm batch shell" >&2
    exit 1
fi
module load openmpi/5.0.3 2>/dev/null || module load openmpi/4.1.6

if [[ -n "${NEKTAR_ENV_FILE:-}" ]]; then
    if [[ ! -r "$NEKTAR_ENV_FILE" ]]; then
        echo "NEKTAR_ENV_FILE is not readable: $NEKTAR_ENV_FILE" >&2
        exit 1
    fi
    # shellcheck disable=SC1090
    source "$NEKTAR_ENV_FILE"
fi

for required in NekMesh CompressibleFlowSolver; do
    if ! command -v "$required" >/dev/null 2>&1; then
        echo "missing Nektar++ program in batch environment: $required" >&2
        exit 1
    fi
    if ! "$required" --version >/dev/null 2>&1; then
        echo "Nektar++ program cannot load in batch environment: $required" >&2
        ldd "$(command -v "$required")" 2>/dev/null | grep 'not found' >&2 || true
        exit 1
    fi
done

BUNDLE_ROOT=${NEKTAR_CASE_ROOT:?NEKTAR_CASE_ROOT is required; submit with scripts/submit.sh}
if [[ "$BUNDLE_ROOT" != /* || ! -f "$BUNDLE_ROOT/scripts/run_pipeline.sh" ]]; then
    echo "invalid NEKTAR_CASE_ROOT: $BUNDLE_ROOT" >&2
    exit 2
fi
RUN_DIR="$BUNDLE_ROOT/runs/${PROFILE_NAME}_a${ALPHA_DEG}_${SLURM_JOB_ID}"

mkdir -p "$RUN_DIR"
env | sort > "$RUN_DIR/slurm_environment.txt"
bash "$BUNDLE_ROOT/scripts/run_pipeline.sh" "$PROFILE_NAME" "$ALPHA_DEG" "$RUN_DIR"
