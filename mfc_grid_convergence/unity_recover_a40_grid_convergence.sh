#!/usr/bin/env bash
set -Eeuo pipefail

DEFAULT_ROOT=/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification
ROOT="${ROOT:-$DEFAULT_ROOT}"
RAW_BASE=https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-f405-grid-study/mfc_grid_convergence

if [[ "$ROOT" != /* || "$ROOT" == / || ! -d "$ROOT/mfc_runs" ]]; then
    echo "ERROR: ROOT must identify the SU2-Diamond-Airfoil-Verification checkout." >&2
    exit 2
fi

RECOVERY_PY=$(mktemp --suffix=.py)
trap 'rm -f "$RECOVERY_PY"' EXIT
curl -fsSL "$RAW_BASE/recover_a40_grid_convergence.py" -o "$RECOVERY_PY"

RECOVERY_PYTHON=python3
if ! "$RECOVERY_PYTHON" -c 'import h5py' >/dev/null 2>&1; then
    LATEST_ENV=$(ls -t "$ROOT"/mfc_runs/fixed_ib_a40_f405_chain_jfm_*/submission.env 2>/dev/null | head -1 || true)
    if [[ -n "$LATEST_ENV" ]]; then
        MFC_ROOT=$(sed -n 's/^MFC_ROOT=//p' "$LATEST_ENV" | head -1)
        MFC_PYTHON="$MFC_ROOT/build/venv/bin/python3"
        if [[ -x "$MFC_PYTHON" ]] && "$MFC_PYTHON" -c 'import h5py' >/dev/null 2>&1; then
            RECOVERY_PYTHON="$MFC_PYTHON"
        fi
    fi
fi
"$RECOVERY_PYTHON" -m py_compile "$RECOVERY_PY"

OUTPUT_DIR="$ROOT/mfc_runs/a40_grid_convergence_recovery_$(date +%Y%m%d-%H%M%S)"
"$RECOVERY_PYTHON" "$RECOVERY_PY" --root "$ROOT" --output-dir "$OUTPUT_DIR"
