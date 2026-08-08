#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
    echo "Usage: $0 ARCHIVE_DIR [STEP] [COMPARE_STEP]" >&2
    exit 2
fi

archive_dir="$(realpath "$1")"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${MFC_IMAGE:-/project/pi_roohie_umass_edu/containers/mfc_latest_cpu.sif}"

[[ -d "$archive_dir/silo_hdf5" ]] || { echo "ERROR: missing $archive_dir/silo_hdf5" >&2; exit 2; }
[[ -s "$image" ]] || { echo "ERROR: missing MFC image $image" >&2; exit 2; }

if [[ $# -eq 1 ]]; then
    mapfile -t saved_steps < <(
        find "$archive_dir/silo_hdf5/p0" -maxdepth 1 -type f -name '*.silo' -printf '%f\n' \
            | sed -n 's/^\([0-9][0-9]*\)\.silo$/\1/p' \
            | sort -n
    )
    if [[ ${#saved_steps[@]} -lt 2 ]]; then
        echo "ERROR: at least two saved Silo steps are required for analysis." >&2
        exit 2
    fi
    step="${saved_steps[-1]}"
    compare_step="${saved_steps[-2]}"
else
    step="$2"
    compare_step="${3:-1500}"
fi

echo "Analyzing saved fields: ${compare_step} -> ${step}"
case "$archive_dir" in
    "$repo_dir"/*) ;;
    *) echo "ERROR: archive must be inside repository $repo_dir" >&2; exit 2 ;;
esac

relative_archive="${archive_dir#"$repo_dir"/}"
output_dir="${archive_dir}/analysis"

module load apptainer/latest
mkdir -p "$output_dir"

apptainer exec --writable-tmpfs \
    --bind "${repo_dir}:/work" \
    --env "MFC_CASE_DIR=/work/${relative_archive}" \
    --env "MFC_ANALYSIS_OUTPUT=/work/${relative_archive}/analysis" \
    --env "MFC_ANALYSIS_STEP=${step}" \
    --env "MFC_ANALYSIS_COMPARE_STEP=${compare_step}" \
    "$image" \
    bash -lc '
        set -euo pipefail
        source /opt/MFC/build/venv/bin/activate
        export PYTHONPATH=/opt/MFC/toolchain
        python /work/mfc_crosscheck/analyze_mfc.py "$MFC_CASE_DIR" \
            --step "$MFC_ANALYSIS_STEP" \
            --compare-step "$MFC_ANALYSIS_COMPARE_STEP" \
            --alpha 30 \
            --output "$MFC_ANALYSIS_OUTPUT"
    '

echo "Analysis complete: $output_dir"
find "$output_dir" -maxdepth 1 -type f -printf '%f\t%s bytes\n' | sort
