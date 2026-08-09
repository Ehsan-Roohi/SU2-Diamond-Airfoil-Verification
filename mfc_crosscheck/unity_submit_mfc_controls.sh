#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Submitting the two required Euler controls with characteristic farfield boundaries."
echo "Control A: alpha=20 deg, coarse grid (attached-shock reference)."
bash "${repo_dir}/mfc_crosscheck/unity_submit_mfc.sh" 20 euler coarse

echo "Control B: alpha=30 deg, coarse grid (high-incidence unsteady reference)."
bash "${repo_dir}/mfc_crosscheck/unity_submit_mfc.sh" 30 euler coarse

echo "Both jobs were submitted. Do not submit the medium case until both controls are analyzed."
