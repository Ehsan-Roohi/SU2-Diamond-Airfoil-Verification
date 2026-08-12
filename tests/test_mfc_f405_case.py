#!/usr/bin/env python3
"""Static and parameter checks for the publication f405 MFC workflow."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path


root = Path(__file__).resolve().parents[1]
case_path = root / "mfc_grid_convergence" / "case_f405.py"
submit_path = root / "mfc_grid_convergence" / "unity_submit_f405.sh"
stl_path = root / "mfc_grid_convergence" / "Diamond_Airfoil_2D_MFC.stl"
validated_stl = root / "mfc_startup_diagnostics" / "Diamond_Airfoil_2D_MFC.stl"

case = json.loads(subprocess.check_output([sys.executable, str(case_path)], text=True))

assert case["m"] + 1 == 4455
assert case["n"] + 1 == 4050
assert math.isclose(case["dt"], 1.0 / 8100.0, rel_tol=0.0, abs_tol=1.0e-15)
assert case["t_step_stop"] == 109350
assert case["t_step_save"] == 4374
assert math.isclose(case["dt"] * case["t_step_stop"], 13.5)
assert math.isclose(case["dt"] * case["t_step_save"], 0.54)
assert case["precision"] == "double"
assert case["viscous"] == "F"
assert case["patch_ib(1)%slip"] == "T"
assert case["ib_state_wrt"] == "T"
assert (case["bc_x%beg"], case["bc_x%end"]) == (-11, -12)
assert (case["bc_y%beg"], case["bc_y%end"]) == (-11, -12)
assert stl_path.read_bytes() == validated_stl.read_bytes()

submit = submit_path.read_text(encoding="utf-8")
required_tokens = (
    '--dependency=afterany:',
    'fixed_ib_a40_f405_jfm_',
    '--no-build',
    'flock -s -w 7200',
    'Number of 2D model boundary edges: *4',
    'RUN_OK_F405.txt',
    'CONSTRAINT="${CONSTRAINT:-intel&x86_64_v4}"',
    'MEMORY="${MEMORY:-360G}"',
    'WALLTIME="${WALLTIME:-3-00:00:00}"',
    'EXPECTED_MFC_COMMIT=0c9a1d434410175ac483b8d71646455444e3b7eb',
    'c6c8b3f62da42ffe1d3318a7cf7c6a5d6b2a1c2c/mfc_grid_convergence',
)
for token in required_tokens:
    assert token in submit, token

print("mfc f405 workflow checks: PASS")
