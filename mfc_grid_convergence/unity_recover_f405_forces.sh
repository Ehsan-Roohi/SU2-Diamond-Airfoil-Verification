#!/usr/bin/env bash
set -euo pipefail

ROOT=/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification
RAW_BASE=https://raw.githubusercontent.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/agent/add-mfc-f405-grid-study/mfc_grid_convergence
CHAIN_BASE=$(ls -dt "$ROOT"/mfc_runs/fixed_ib_a40_f405_chain_jfm_* 2>/dev/null | head -1)

if [[ -z "$CHAIN_BASE" || ! -f "$CHAIN_BASE/submission.env" ]]; then
    echo "ERROR: latest f405 chain or submission.env was not found" >&2
    exit 1
fi

# Parse only the needed value. Do not source older submission.env files because
# an unquoted '&' in CONSTRAINT can be interpreted as shell control syntax.
CASE_DIR=$(sed -n 's/^CASE_DIR=//p' "$CHAIN_BASE/submission.env" | head -1)
if [[ -z "$CASE_DIR" || ! -d "$CASE_DIR/restart_data" ]]; then
    echo "ERROR: f405 restart_data was not found: $CASE_DIR" >&2
    exit 1
fi

EXTRACTOR=$(mktemp --suffix=.py)
trap 'rm -f "$EXTRACTOR"' EXIT
curl -fsSL "$RAW_BASE/extract_f405_ib_forces.py" -o "$EXTRACTOR"
python3 -m py_compile "$EXTRACTOR"

OUT="$CHAIN_BASE/f405_force_recovery_$(date +%Y%m%d-%H%M%S)"
python3 "$EXTRACTOR" "$CASE_DIR" --output-dir "$OUT"

(
    cd "$OUT"
    zip -9 MFC_A40_F405_FORCE_RECOVERY.zip \
        MFC_A40_F405_FORCE_HISTORY.csv \
        MFC_A40_F405_FORCE_SUMMARY.json
    sha256sum MFC_A40_F405_FORCE_RECOVERY.zip | tee MFC_A40_F405_FORCE_RECOVERY.zip.sha256.txt
)

echo "FORCE_RECOVERY_DIR=$OUT"
echo "UPLOAD_THIS=$OUT/MFC_A40_F405_FORCE_RECOVERY.zip"
echo "UPLOAD_SHA256=$OUT/MFC_A40_F405_FORCE_RECOVERY.zip.sha256.txt"
