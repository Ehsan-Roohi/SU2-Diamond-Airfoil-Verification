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
echo "bundle tests: PASS"
