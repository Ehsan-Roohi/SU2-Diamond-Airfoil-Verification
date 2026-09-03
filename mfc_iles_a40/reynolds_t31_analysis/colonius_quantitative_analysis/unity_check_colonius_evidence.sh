#!/usr/bin/env bash
set -Eeuo pipefail

output=${1:?usage: unity_check_colonius_evidence.sh /path/to/output}
job_file="$output/JOB_ID.txt"

[[ -d "$output" ]] || { echo "ERROR: output directory not found: $output" >&2; exit 2; }
[[ -s "$job_file" ]] || { echo "ERROR: missing $job_file" >&2; exit 2; }
job_id=$(<"$job_file")

printf 'OUTPUT=%s\nJOB_ID=%s\n' "$output" "$job_id"
echo '===== SQUEUE ====='
squeue -j "$job_id" || true
echo '===== SACCT ====='
sacct -j "$job_id" -X \
  --format=JobID%18,JobName%20,State%20,Elapsed,ExitCode,MaxRSS,NodeList%22 || true

echo '===== LOG TAIL ====='
if [[ -f "$output/slurm-$job_id.out" ]]; then
  tail -n 80 "$output/slurm-$job_id.out"
else
  echo "WAIT: $output/slurm-$job_id.out"
fi

if [[ -s "$output/ANALYSIS_OK.txt" && -s "$output/TIM_COLONIUS_QUANTITATIVE_CHECKS.pdf" ]]; then
  echo 'TIM_COLONIUS_POSTPROCESS_CHECK=PASS'
  printf 'PDF=%s\n' "$output/TIM_COLONIUS_QUANTITATIVE_CHECKS.pdf"
  exit 0
fi

if [[ -s "$output/ANALYSIS_FAILED.txt" ]]; then
  echo '===== FAILURE ====='
  cat "$output/ANALYSIS_FAILED.txt"
  [[ -f "$output/slurm-$job_id.err" ]] && tail -n 120 "$output/slurm-$job_id.err"
  echo 'TIM_COLONIUS_POSTPROCESS_CHECK=FAILED'
  exit 1
fi

echo 'TIM_COLONIUS_POSTPROCESS_CHECK=RUNNING_OR_PENDING'
