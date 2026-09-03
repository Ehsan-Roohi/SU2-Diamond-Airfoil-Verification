#!/usr/bin/env bash
set -Eeuo pipefail

default_parent=/scratch4/workspace/roohie_umass_edu-mfc-a40-cv
if (($#)); then
    output=$1
else
    pointer="$default_parent/LAST_TIM_COLONIUS_NATIVE_FORCE_OUTPUT.txt"
    [[ -s "$pointer" ]] || { echo "ERROR: pass output path or create $pointer first" >&2; exit 2; }
    output=$(<"$pointer")
fi

[[ -d "$output" ]] || { echo "ERROR: output directory not found: $output" >&2; exit 2; }
[[ -s "$output/JOB_ID.txt" ]] || { echo "ERROR: missing $output/JOB_ID.txt" >&2; exit 2; }
job_id=$(<"$output/JOB_ID.txt")
archive="$(dirname -- "$output")/TIM_COLONIUS_NATIVE_FORCES_JOB${job_id}.zip"

printf 'OUTPUT=%s\nJOB_ID=%s\n' "$output" "$job_id"
echo '===== SQUEUE ====='
squeue -j "$job_id" || true
echo '===== SACCT ====='
sacct -j "$job_id" -X \
    --format=JobID%18,JobName%20,State%20,Elapsed,ExitCode,MaxRSS,NodeList%22 || true

echo '===== LOG TAIL ====='
if [[ -f "$output/slurm-$job_id.out" ]]; then
    tail -n 100 "$output/slurm-$job_id.out"
else
    echo "WAIT: $output/slurm-$job_id.out"
fi

if [[ -s "$output/ANALYSIS_COMPLETE.txt" && -s "$archive" ]]; then
    echo '===== COMPLETION MARKER ====='
    cat "$output/ANALYSIS_COMPLETE.txt"
    [[ -s "$archive.sha256.txt" ]] && (cd -- "$(dirname -- "$archive")" && sha256sum -c "$(basename -- "$archive.sha256.txt")")
    status=$(awk -F= '$1=="status" {print $2; exit}' "$output/ANALYSIS_COMPLETE.txt")
    case "$status" in
        PASS) echo 'MFC_NATIVE_FORCE_CHECK=PASS' ;;
        PARTIAL) echo 'MFC_NATIVE_FORCE_CHECK=PARTIAL_DATA_GAPS_REPORTED' ;;
        *) echo "MFC_NATIVE_FORCE_CHECK=INVALID_STATUS_$status"; exit 1 ;;
    esac
    printf 'EMAIL_FIGURE=%s\nPDF=%s\nSUMMARY=%s\nARCHIVE=%s\n' \
        "$output/TIM_COLONIUS_REYNOLDS_FORCES.png" \
        "$output/TIM_COLONIUS_NATIVE_FORCES.pdf" \
        "$output/native_force_summary.csv" \
        "$archive"
    exit 0
fi

if [[ -s "$output/ANALYSIS_FAILED.txt" ]]; then
    echo '===== FAILURE ====='
    cat "$output/ANALYSIS_FAILED.txt"
    [[ -f "$output/slurm-$job_id.err" ]] && tail -n 160 "$output/slurm-$job_id.err"
    echo 'MFC_NATIVE_FORCE_CHECK=FAILED'
    exit 1
fi

echo 'MFC_NATIVE_FORCE_CHECK=RUNNING_OR_PENDING'
