#!/usr/bin/env bash
set -Eeuo pipefail

dataset=${1:?usage: unity_submit_colonius_evidence.sh /path/to/ml_dataset [output_dir]}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
analyzer="$script_dir/analyze_colonius_evidence.py"
runner="$script_dir/run_colonius_evidence.sbatch"
python_bin=${PYTHON_BIN:-$(command -v python3)}
timestamp=$(date -u +%Y%m%d-%H%M%S)
output=${2:-"$(dirname -- "$dataset")/tim_colonius_quantitative_checks_$timestamp"}

[[ -f "$dataset/DATASET_OK.txt" ]] || { echo "ERROR: missing $dataset/DATASET_OK.txt" >&2; exit 2; }
[[ -f "$dataset/manifest.jsonl" ]] || { echo "ERROR: missing $dataset/manifest.jsonl" >&2; exit 2; }
[[ -x "$python_bin" ]] || { echo "ERROR: python3 is unavailable" >&2; exit 2; }
mkdir -p "$output"

"$python_bin" "$analyzer" --self-test >/dev/null
job_token=$(sbatch --parsable \
    --output="$output/slurm-%j.out" \
    --error="$output/slurm-%j.err" \
    --export="ALL,DATASET=$dataset,OUTPUT=$output,ANALYZER=$analyzer,PYTHON_BIN=$python_bin" \
    "$runner")
job_id=${job_token%%;*}

printf '%s\n' "$job_id" >"$output/JOB_ID.txt"
cat >"$output/SUBMISSION.env" <<EOF
status=SUBMITTED
job_id=$job_id
dataset=$dataset
output=$output
python=$python_bin
submitted_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

printf 'TIM_COLONIUS_POSTPROCESS_SUBMITTED=PASS\nJOB_ID=%s\nOUTPUT=%s\nWATCH=squeue -j %s\nCHECK=bash %s/unity_check_colonius_evidence.sh %s\nFINAL=%s\n' \
    "$job_id" "$output" "$job_id" "$script_dir" "$output" \
    "$output/TIM_COLONIUS_QUANTITATIVE_CHECKS.pdf"
