#!/usr/bin/env bash
set -Eeuo pipefail

PROFILE=${1:-smoke}
ALPHA_DEG=${2:-4}

case "$PROFILE" in
    smoke|pilot|production|span_sensitivity) ;;
    *)
        echo "profile must be smoke, pilot, production, or span_sensitivity" >&2
        exit 2
        ;;
esac
case "$ALPHA_DEG" in
    0|4|8) ;;
    *)
        echo "angle of attack must be 0, 4, or 8 degrees" >&2
        exit 2
        ;;
esac

REPOSITORY=${NEKTAR_CASE_REPOSITORY:-Ehsan-Roohi/SU2-Diamond-Airfoil-Verification}
REF=${NEKTAR_CASE_REF:-agent/add-nektar-implicit-iles}
PROJECT_ROOT=${UNITY_PROJECT_ROOT:-/project/pi_roohie_umass_edu}
CHECKOUT_ROOT=${NEKTAR_CASE_CHECKOUT:-$PROJECT_ROOT/github/SU2-Diamond-Airfoil-Verification-nektar}
INSTALL_PREFIX=${NEKTAR_INSTALL_PREFIX:-$PROJECT_ROOT/apps/nektar-5.10}

if [[ "$PROJECT_ROOT" != /* || "$PROJECT_ROOT" == "/" ]]; then
    echo "UNITY_PROJECT_ROOT must be a non-root absolute path" >&2
    exit 2
fi

if ! command -v git >/dev/null 2>&1; then
    echo "git is required on the Unity login node" >&2
    exit 1
fi

if [[ -d "$CHECKOUT_ROOT/.git" ]]; then
    git -C "$CHECKOUT_ROOT" fetch --depth 1 origin "$REF"
    if ! git -C "$CHECKOUT_ROOT" diff --quiet || \
       ! git -C "$CHECKOUT_ROOT" diff --cached --quiet; then
        echo "local changes found in $CHECKOUT_ROOT; refusing to overwrite them" >&2
        exit 1
    fi
    git -C "$CHECKOUT_ROOT" checkout -B "$REF" FETCH_HEAD
else
    if [[ -e "$CHECKOUT_ROOT" ]]; then
        echo "$CHECKOUT_ROOT exists but is not a Git checkout" >&2
        exit 1
    fi
    mkdir -p "$(dirname "$CHECKOUT_ROOT")"
    git clone --depth 1 --branch "$REF" \
        "https://github.com/${REPOSITORY}.git" "$CHECKOUT_ROOT"
fi

CASE_ROOT="$CHECKOUT_ROOT/nektar_implicit_iles"
if [[ ! -f "$CASE_ROOT/scripts/submit.sh" ]]; then
    echo "case folder not found after checkout: $CASE_ROOT" >&2
    exit 1
fi

# Unity's module command is normally initialized by the login shell.
if type module >/dev/null 2>&1; then
    if ! command -v mpicxx >/dev/null 2>&1; then
        module load openmpi/5.0.3 2>/dev/null || \
            module load openmpi/4.1.6
    fi
    if ! command -v cmake >/dev/null 2>&1; then
        module load cmake
    fi
fi

ENV_FILE="$INSTALL_PREFIX/nektar_env.sh"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
fi

if ! command -v CompressibleFlowSolver >/dev/null 2>&1 || \
   ! command -v NekMesh >/dev/null 2>&1; then
    bash "$CASE_ROOT/scripts/install_nektar_5.10_unity.sh" "$INSTALL_PREFIX"
    # shellcheck disable=SC1090
    source "$ENV_FILE"
fi

for required in gmsh NekMesh CompressibleFlowSolver sbatch python3; do
    if ! command -v "$required" >/dev/null 2>&1; then
        echo "required command is unavailable after setup: $required" >&2
        exit 1
    fi
done

export NEKTAR_ENV_FILE="$ENV_FILE"
echo "Submitting Nektar++ ILES profile=$PROFILE alpha=$ALPHA_DEG"
bash "$CASE_ROOT/scripts/submit.sh" "$PROFILE" "$ALPHA_DEG"

