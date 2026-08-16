#!/usr/bin/env python3
"""Regression checks for the clean f180 MFC force-repair workflow."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path


root = Path(__file__).resolve().parents[1]
case_path = root / "mfc_grid_convergence" / "case_f180_force_repair.py"
submit_path = root / "mfc_grid_convergence" / "unity_submit_f180_force_repair.sh"

case = json.loads(subprocess.check_output([sys.executable, str(case_path)], text=True))
assert case["m"] + 1 == 1980
assert case["n"] + 1 == 1800
assert math.isclose(case["dt"], 1.0 / 3600.0, rel_tol=0.0, abs_tol=1.0e-15)
assert case["t_step_start"] == 0
assert case["t_step_stop"] == 48600
assert case["t_step_save"] == 1944
assert math.isclose(case["dt"] * case["t_step_stop"], 13.5)
assert math.isclose(case["dt"] * case["t_step_save"], 0.54)
assert case["precision"] == "double"
assert case["viscous"] == "F"
assert case["patch_ib(1)%slip"] == "T"
assert case["ib_state_wrt"] == "T"
assert (case["bc_x%beg"], case["bc_x%end"]) == (-11, -12)
assert (case["bc_y%beg"], case["bc_y%end"]) == (-11, -12)

submit = submit_path.read_text(encoding="utf-8")
for token in (
    'MEMORY="${MEMORY:-32G}"',
    'WALLTIME="${WALLTIME:-12:00:00}"',
    'QOS="${QOS:-}"',
    'qos_args+=(--qos="$QOS")',
    'CONSTRAINT="${CONSTRAINT:-intel&x86_64_v4}"',
    "EXPECTED_MFC_COMMIT=0c9a1d434410175ac483b8d71646455444e3b7eb",
    "fixed_ib_a40_f180_force_repair_jfm_",
    "--no-build",
    'exec flock -s -w 7200 "$LOCK_FILE" bash "$0"',
    "RUN_OK_F180_FORCE_REPAIR.txt",
    "F180_FORCE_GATE=PASS",
    "maximum <= 1.0e-10",
):
    assert token in submit, token

print("mfc f180 force-repair workflow checks: PASS")
