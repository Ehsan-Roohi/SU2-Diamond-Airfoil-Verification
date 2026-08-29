#!/usr/bin/env bash
set -Eeuo pipefail
trap 'rc=$?; trap - ERR; echo "ERROR: article submitter stopped at line $LINENO (exit $rc)." >&2; exit "$rc"' ERR

PROJECT_ROOT=${PROJECT_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}
REPO_ROOT=${REPO_ROOT:-/project/pi_roohie_umass_edu/github_sync/KineticGaussian/SU2-Diamond-Airfoil-Verification}
MFC_ROOT=${MFC_ROOT:-$REPO_ROOT/third_party/MFC-0c9a1d43-iles-portable-v3}
RUN_ROOT=${RUN_ROOT:-$PROJECT_ROOT/runs/mfc_iles_a40_fresh_hll_production}
CASE_DIR=${CASE_DIR:-}
SCRIPT=$REPO_ROOT/mfc_iles_a40/hll_production_analysis/analyze_mfc_hll_article.py
SU2_ROOT=${SU2_ROOT:-$PROJECT_ROOT/runs/urans_alpha40/medium_halfdt}
SU2_CONFIG=${SU2_CONFIG:-}
NEKTAR_SUMMARY=${NEKTAR_SUMMARY:-}
ANALYSIS_START=${ANALYSIS_START:-3.0}
MEMORY=${MEMORY:-32G}
WALLTIME=${WALLTIME:-06:00:00}
CONSTRAINT=${CONSTRAINT:-intel&x86_64_v4}

# Consume the complete sorted stream.  Using ``head -n1`` here makes the
# upstream ``sort`` receive SIGPIPE when more than one candidate exists;
# under ``set -o pipefail`` that aborts the submitter before ``sbatch``.
if [[ -z "$CASE_DIR" && -d "$RUN_ROOT" ]]; then
    mapfile -t case_candidates < <(
        find "$RUN_ROOT" -mindepth 1 -maxdepth 1 -type d \
            -name 'f270_t000_t0600_w5unmapped_hll_dt1_*' \
            -printf '%T@ %p\n' 2>/dev/null | sort -nr
    )
    if ((${#case_candidates[@]})); then
        CASE_DIR=${case_candidates[0]#* }
    fi
fi

[[ -n "$CASE_DIR" && -d "$CASE_DIR" ]] || { echo "ERROR: HLL production case was not found." >&2; exit 2; }
[[ -f "$CASE_DIR/RUN_OK_INITIAL.txt" ]] || { echo "ERROR: successful-run marker is missing." >&2; exit 2; }
[[ -s "$CASE_DIR/restart_data/lustre_32400.dat" ]] || { echo "ERROR: final t=6 field is missing." >&2; exit 2; }
[[ -x "$MFC_ROOT/mfc.sh" ]] || { echo "ERROR: pinned MFC checkout was not found: $MFC_ROOT" >&2; exit 2; }
[[ -f "$SCRIPT" ]] || { echo "ERROR: article analyzer was not found: $SCRIPT" >&2; exit 2; }
python3 -m py_compile "$SCRIPT"

python3 - "$CASE_DIR/case.py" <<'PY'
import json
import subprocess
import sys

case = json.loads(subprocess.check_output([
    sys.executable, sys.argv[1], "--mode", "initial", "--grid", "f270",
    "--start-time", "0", "--final-time", "6", "--save-dt", "0.05",
    "--dt-factor", "1", "--format", "binary",
], text=True))
expected = {
    "m": 2969,
    "n": 2699,
    "t_step_start": 0,
    "t_step_stop": 32400,
    "t_step_save": 270,
    "riemann_solver": 1,
    "weno_order": 5,
    "mapped_weno": "F",
    "viscous": "T",
    "ib_state_wrt": "T",
}
for key, value in expected.items():
    if case.get(key) != value:
        raise SystemExit(f"ERROR: {key}={case.get(key)!r}; expected {value!r}")
print("HLL_ARTICLE_PREFLIGHT=PASS")
PY

if [[ -z "$SU2_CONFIG" && -d "$SU2_ROOT" ]]; then
    mapfile -t su2_config_candidates < <(
        find "$SU2_ROOT" -type f -name '*.cfg' -printf '%T@ %p\n' \
            2>/dev/null | sort -nr
    )
    if ((${#su2_config_candidates[@]})); then
        SU2_CONFIG=${su2_config_candidates[0]#* }
    fi
fi

STAMP=$(date +%Y%m%d-%H%M%S)
OUT_DIR=$CASE_DIR/article_diagnostics_$STAMP
mkdir -p "$OUT_DIR"
SBATCH_FILE=$OUT_DIR/run_article_post.sbatch

cat >"$SBATCH_FILE" <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4

set -Eeuo pipefail
: "${CASE_DIR:?}"
: "${MFC_ROOT:?}"
: "${SCRIPT:?}"
: "${OUT_DIR:?}"

module purge
module load openmpi/5.0.3
export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-1}
export MPLCONFIGDIR="$OUT_DIR/matplotlib-cache"
mkdir -p "$MPLCONFIGDIR"

args=(
    "$CASE_DIR"
    --mfc-root "$MFC_ROOT"
    --output-dir "$OUT_DIR"
    --dt 0.0001851851851851852
    --analysis-start "$ANALYSIS_START"
    --alpha 40
    --rho-inf 1
    --u-inf 3
    --chord 1
    --reynolds 1000000
)
if [[ -d "${SU2_ROOT:-}" && -f "${SU2_CONFIG:-}" ]]; then
    args+=(--su2-history "$SU2_ROOT" --su2-config "$SU2_CONFIG")
fi
if [[ -n "${NEKTAR_SUMMARY:-}" && -f "$NEKTAR_SUMMARY" ]]; then
    args+=(--nektar-summary "$NEKTAR_SUMMARY")
fi

python3 "$SCRIPT" "${args[@]}" 2>&1 | tee "$OUT_DIR/article-post.log"
test -s "$OUT_DIR/mfc_hll_article_metrics.json"
test -s "$OUT_DIR/mfc_hll_force_history.csv"
test -s "$OUT_DIR/mfc_hll_shock_history.csv"
test -s "$OUT_DIR/article_solver_comparison.csv"

python3 - "$OUT_DIR/mfc_hll_article_metrics.json" <<'PY'
import json
import sys

metrics = json.load(open(sys.argv[1], encoding="utf-8"))
assert metrics["force_statistics"]["CL"]["samples"] >= 16
assert metrics["force_statistics"]["CD"]["samples"] >= 16
assert metrics["shock_statistics"]["detected_samples"] >= 4
assert metrics["shedding"]["dominant_frequency"] > 0
assert metrics["shedding"]["strouhal"] > 0
print("ARTICLE_OUTPUT_GATE=PASS")
PY

(
    cd "$OUT_DIR"
    products=(
        ARTICLE_SUMMARY.txt \
        article_solver_comparison.csv \
        mfc_hll_article_metrics.json \
        mfc_hll_force_history.csv \
        mfc_hll_lift_spectrum.csv \
        mfc_hll_shock_history.csv \
        mfc_hll_force_history.png \
        mfc_hll_lift_spectrum.png \
        mfc_hll_shock_history.png \
        mfc_hll_final_shock_fit.png \
        article-post.log
    )
    for optional in su2_standardized_history.csv su2_standardized_summary.json; do
        [[ -f "$optional" ]] && products+=("$optional")
    done
    zip -9 MFC_A40_HLL_T6_ARTICLE_DIAGNOSTICS.zip "${products[@]}"
    test -s MFC_A40_HLL_T6_ARTICLE_DIAGNOSTICS.zip
    sha256sum MFC_A40_HLL_T6_ARTICLE_DIAGNOSTICS.zip >MFC_A40_HLL_T6_ARTICLE_DIAGNOSTICS.zip.sha256.txt
)
touch "$OUT_DIR/ARTICLE_POST_OK.txt"
echo "ARTICLE_POST=PASS"
echo "UPLOAD=$OUT_DIR/MFC_A40_HLL_T6_ARTICLE_DIAGNOSTICS.zip"
echo "SHA256=$OUT_DIR/MFC_A40_HLL_T6_ARTICLE_DIAGNOSTICS.zip.sha256.txt"
SBATCH

JOB=$(sbatch --parsable \
    --mem="$MEMORY" \
    --time="$WALLTIME" \
    --constraint="$CONSTRAINT" \
    --job-name=mfc-a40-article \
    --output="$OUT_DIR/slurm-%j.out" \
    --error="$OUT_DIR/slurm-%j.err" \
    --export="ALL,CASE_DIR=$CASE_DIR,MFC_ROOT=$MFC_ROOT,SCRIPT=$SCRIPT,OUT_DIR=$OUT_DIR,ANALYSIS_START=$ANALYSIS_START,SU2_ROOT=$SU2_ROOT,SU2_CONFIG=$SU2_CONFIG,NEKTAR_SUMMARY=$NEKTAR_SUMMARY" \
    "$SBATCH_FILE")
JOB=${JOB%%;*}

printf 'ARTICLE_POST_JOB=%s\nCASE_DIR=%s\nOUTPUT_DIR=%s\nWATCH=squeue -j %s\n' "$JOB" "$CASE_DIR" "$OUT_DIR" "$JOB"
