#!/usr/bin/env bash
# Export the latest Unity AoA 0/4/8 run and push compact evidence to GitHub.
set -Eeuo pipefail

branch="${BRANCH:-agent/su2-a048-full-dataset}"
remote="${REMOTE_URL:-https://github.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification.git}"
scratch_parent="${SCRATCH_PARENT:-/scratch4/workspace/roohie_umass_edu-mfc-a40-cv}"
run_parent="${RUN_PARENT:-$scratch_parent/su2-a048-data/runs/euler_a048_full}"
publish_parent="${PUBLISH_PARENT:-$scratch_parent/su2-a048-github-publish}"
python_bin="${PYTHON_BIN:-$(command -v python3)}"

if [[ -z "${RUN_ROOT:-}" ]]; then
    if [[ -L "$run_parent/latest" ]]; then
        RUN_ROOT="$(readlink -f "$run_parent/latest")"
    else
        RUN_ROOT="$(find "$run_parent" -mindepth 1 -maxdepth 1 -type d -printf '%f\t%p\n' 2>/dev/null | sort -r | head -n 1 | cut -f2-)"
    fi
fi
[[ -n "${RUN_ROOT:-}" && -d "$RUN_ROOT" ]] || {
    echo "ERROR: AoA run directory was not found. Set RUN_ROOT explicitly." >&2
    exit 2
}

"$python_bin" - <<'PY'
import importlib
for module in ("numpy", "matplotlib"):
    importlib.import_module(module)
print("A048_PLOT_PREFLIGHT=PASS")
PY

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
checkout="$publish_parent/checkout-$stamp"
mkdir -p "$publish_parent"
git clone --depth 1 --single-branch --branch "$branch" "$remote" "$checkout"

campaign="$(basename "$(readlink -f "$RUN_ROOT")" | tr -cs 'A-Za-z0-9._-' '_')"
result_root="$checkout/results/su2_euler_m3_aoa_0_4_8"
campaign_dir="$result_root/campaigns/$campaign"

"$python_bin" "$checkout/scripts/export_euler_a048_results.py" \
    --run-root "$RUN_ROOT" \
    --output-dir "$campaign_dir"

"$python_bin" - "$result_root" "$campaign" <<'PY'
import sys
from pathlib import Path
root, campaign = Path(sys.argv[1]), sys.argv[2]
(root / "LATEST.md").write_text(
    "# Latest published AoA campaign\n\n"
    f"[{campaign}](campaigns/{campaign}/README.md)\n",
    encoding="utf-8",
)
PY

oversized="$(find "$result_root" -type f -size +25M -print -quit)"
[[ -z "$oversized" ]] || {
    echo "ERROR: refusing to commit oversized result file: $oversized" >&2
    exit 3
}
total_bytes="$(du -sb "$result_root" | awk '{print $1}')"
(( total_bytes < 100000000 )) || {
    echo "ERROR: compact result section exceeds 100 MB ($total_bytes bytes)." >&2
    exit 3
}

git -C "$checkout" add results/su2_euler_m3_aoa_0_4_8
if git -C "$checkout" diff --cached --quiet; then
    echo "A048_GITHUB_RESULTS=NO_CHANGES"
    exit 0
fi
git -C "$checkout" config user.name "${GIT_AUTHOR_NAME:-Ehsan Roohi}"
git -C "$checkout" config user.email "${GIT_AUTHOR_EMAIL:-roohie@umass.edu}"
git -C "$checkout" commit -m "Publish SU2 Mach-3 AoA 0/4/8 results"
git -C "$checkout" push origin "HEAD:$branch"

commit="$(git -C "$checkout" rev-parse HEAD)"
echo "A048_GITHUB_RESULTS=PASS"
echo "SOURCE_RUN=$(readlink -f "$RUN_ROOT")"
echo "COMMIT=$commit"
echo "RESULTS_URL=https://github.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/tree/$branch/results/su2_euler_m3_aoa_0_4_8"
echo "CHECKOUT_RETAINED=$checkout"
