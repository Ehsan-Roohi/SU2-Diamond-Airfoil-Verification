#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python3 "$ROOT/tests/test_geometry.py"
python3 "$ROOT/tests/test_postprocessor.py"
python3 -m py_compile \
    "$ROOT/geometry/generate_geo.py" \
    "$ROOT/scripts/render_session.py" \
    "$ROOT/scripts/preflight.py" \
    "$ROOT/scripts/scan_solver_log.py" \
    "$ROOT/post/analyze_forces.py"
bash -n \
    "$ROOT/scripts/run_pipeline.sh" \
    "$ROOT/scripts/submit.sh" \
    "$ROOT/scripts/install_nektar_5.10_unity.sh" \
    "$ROOT/slurm/run.slurm"

(
    set -u
    source "$ROOT/profiles/smoke.env"
    [[ "$JOB_NODES" == "1" ]]
    [[ "$JOB_TASKS_PER_NODE" == "8" ]]
    [[ "$JOB_MEMORY" == "16G" ]]
    [[ "$JOB_EXCLUSIVE" == "0" ]]
)
if grep -q '^#SBATCH --exclusive' "$ROOT/slurm/run.slurm"; then
    echo "run.slurm must not force exclusive nodes for every profile" >&2
    exit 1
fi
echo "bundle tests: PASS"
