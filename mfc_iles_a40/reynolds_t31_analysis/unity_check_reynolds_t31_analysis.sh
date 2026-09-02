#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT=${PROJECT_ROOT:-/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data}
ANALYSIS_PARENT=${ANALYSIS_PARENT:-$PROJECT_ROOT/analysis}
ANALYSIS_ROOT=${ANALYSIS_ROOT:-}

if [[ -z "$ANALYSIS_ROOT" ]]; then
    mapfile -t candidates < <(
        find "$ANALYSIS_PARENT" -mindepth 1 -maxdepth 1 -type d \
            -name 'mfc_a40_reynolds_t31_*' -printf '%T@ %p\n' 2>/dev/null | sort -nr
    )
    ((${#candidates[@]})) || { echo "ERROR: no Reynolds/t31 analysis directory was found." >&2; exit 2; }
    ANALYSIS_ROOT=${candidates[0]#* }
fi

submission="$ANALYSIS_ROOT/SUBMISSION.env"
[[ -f "$submission" ]] || { echo "ERROR: missing submission record: $submission" >&2; exit 2; }
value() {
    awk -F= -v wanted="$1" '$1 == wanted {print substr($0, index($0, "=") + 1); exit}' "$submission"
}

prepare_job=$(value prepare_job)
analysis_job=$(value analysis_array_job)
visual_job=$(value visual_job)
aggregate_job=$(value aggregate_job)
jobs="$prepare_job,$analysis_job,$visual_job,$aggregate_job"

echo "ANALYSIS_ROOT=$ANALYSIS_ROOT"
echo "JOBS=$jobs"
echo "===== SQUEUE ====="
squeue -j "$jobs" 2>/dev/null || true
echo "===== SACCT ====="
sacct -j "$jobs" -X --format=JobID%18,JobName%22,State%20,Elapsed,ExitCode,NodeList%22 2>/dev/null || true

echo "===== MARKERS ====="
markers=(
    long_view/LONG_VIEW_OK.txt
    cases/re1e4_f180/ANALYSIS_OK.txt
    cases/re1e4_f270/ANALYSIS_OK.txt
    cases/re5e4_f180/ANALYSIS_OK.txt
    cases/re1e5_f180/ANALYSIS_OK.txt
    cases/re1e6_f270/ANALYSIS_OK.txt
    cases/re1e6_long_t31/ANALYSIS_OK.txt
    visuals/VISUALIZATION_OK.txt
    ANALYSIS_COMPLETE.txt
)
for relative in "${markers[@]}"; do
    if [[ -s "$ANALYSIS_ROOT/$relative" ]]; then
        printf 'PASS  %s\n' "$relative"
    else
        printf 'WAIT  %s\n' "$relative"
    fi
done

mapfile -t failures < <(find "$ANALYSIS_ROOT" -type f -name '*FAILED*.txt' -print 2>/dev/null | sort)
if ((${#failures[@]})); then
    echo "===== FAILURE MARKERS ====="
    for path in "${failures[@]}"; do
        echo "$path"
        sed -n '1,40p' "$path"
    done
fi

if [[ -s "$ANALYSIS_ROOT/ANALYSIS_COMPLETE.txt" ]]; then
    echo "===== SCIENTIFIC SUMMARY ====="
    sed -n '1,240p' "$ANALYSIS_ROOT/summary/MFC_REYNOLDS_T31_SUMMARY.md"
    echo "===== DELIVERABLES ====="
    ls -lh "$ANALYSIS_ROOT/MFC_REYNOLDS_T31_ANALYSIS_CORE.zip" \
        "$ANALYSIS_ROOT/visuals/"*.mp4
    (
        cd "$ANALYSIS_ROOT"
        sha256sum -c "MFC_REYNOLDS_T31_ANALYSIS_CORE.zip.sha256.txt"
    )
    echo "MFC_REYNOLDS_T31_CHECK=COMPLETE"
elif ((${#failures[@]})); then
    echo "MFC_REYNOLDS_T31_CHECK=FAILED"
    exit 1
else
    echo "MFC_REYNOLDS_T31_CHECK=RUNNING_OR_QUEUED"
fi
