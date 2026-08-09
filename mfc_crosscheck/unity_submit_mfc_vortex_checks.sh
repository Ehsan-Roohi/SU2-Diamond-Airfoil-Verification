#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Submitting the two Euler vortex-mechanism checks in isolated case directories."
echo "Check A: alpha=30 deg, medium grid (2x linear resolution versus coarse)."
bash "${repo_dir}/mfc_crosscheck/unity_submit_mfc.sh" 30 euler medium

echo "Check B: alpha=0 deg, coarse grid (matched-resolution symmetric-incidence control)."
bash "${repo_dir}/mfc_crosscheck/unity_submit_mfc.sh" 0 euler coarse

echo "Both jobs were submitted. Interpret them together with the new alpha=30 coarse control."
