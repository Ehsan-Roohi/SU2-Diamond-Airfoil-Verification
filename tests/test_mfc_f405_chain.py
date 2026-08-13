#!/usr/bin/env python3
"""Regression checks for the three-stage f405 Unity workflow."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path


root = Path(__file__).resolve().parents[1]
case_path = root / "mfc_grid_convergence" / "case_f405_restartable.py"
submit_path = root / "mfc_grid_convergence" / "unity_submit_f405_chain.sh"

segments = ((0, 34992), (34992, 69984), (69984, 109350))
for index, (start, stop) in enumerate(segments, 1):
    case = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                str(case_path),
                "--start-step",
                str(start),
                "--stop-step",
                str(stop),
                "--save-every",
                "4374",
            ],
            text=True,
        )
    )
    assert case["m"] + 1 == 4455
    assert case["n"] + 1 == 4050
    assert math.isclose(case["dt"], 1.0 / 8100.0, abs_tol=1.0e-15)
    assert case["t_step_start"] == start
    assert case["t_step_stop"] == stop
    assert case["t_step_save"] == 4374
    assert stop % case["t_step_save"] == 0
    assert case["precision"] == "double"
    assert case["viscous"] == "F"
    assert case["patch_ib(1)%slip"] == "T"
    assert case["ib_state_wrt"] == "T"
    assert (case["bc_x%beg"], case["bc_x%end"]) == (-11, -12)
    assert (case["bc_y%beg"], case["bc_y%end"]) == (-11, -12)
    if index == 1:
        assert case["num_patches"] == 1
        assert "old_ic" not in case
        assert "old_grid" not in case
    else:
        assert case["num_patches"] == 0
        assert case["old_ic"] == "T"
        assert case["old_grid"] == "T"
        assert case["t_step_old"] == 0

assert [stop / 8100 for _, stop in segments] == [4.32, 8.64, 13.5]

submit = submit_path.read_text(encoding="utf-8")
required_tokens = (
    'MEMORY="${MEMORY:-120G}"',
    'WALLTIME="${WALLTIME:-1-00:00:00}"',
    'NTASKS="${NTASKS:-48}"',
    'CONSTRAINT="${CONSTRAINT:-intel&x86_64_v4}"',
    "SEGMENT_STARTS=(0 34992 69984)",
    "SEGMENT_STOPS=(34992 69984 109350)",
    '--dependency="afterok:${previous_job}"',
    '--dependency="afterany:${AFTER_JOB}"',
    "restart_data/lustre_${START_STEP}.dat",
    "restart_data/lustre_${STOP_STEP}.dat",
    "RUN_OK_F405.txt",
    "Number of 2D model boundary edges: *4",
    "--no-build",
    'exec flock -s -w 7200 "$LOCK_FILE" bash "$0"',
    '[[ "${MFC_LOCK_HELD:-0}" != 1 ]]',
    "printf '%s=%q\\n'",
    'write_env CONSTRAINT "$CONSTRAINT"',
    "EXPECTED_MFC_COMMIT=0c9a1d434410175ac483b8d71646455444e3b7eb",
    "a10399ec9d9cf0b65bcb8eadd116054a15bbcc07/mfc_grid_convergence",
)
for token in required_tokens:
    assert token in submit, token

assert "flock -s -w 7200 9" not in submit
assert "CONSTRAINT=$CONSTRAINT" not in submit

# The exact shell escaping used by submission.env must remain source-safe for
# Slurm feature expressions containing '&'.
env_text = subprocess.check_output(
    ["bash", "-c", "printf 'CONSTRAINT=%q\\n' 'intel&x86_64_v4'"], text=True
)
round_trip = subprocess.check_output(
    ["bash", "-c", "source /dev/stdin; printf %s \"$CONSTRAINT\""],
    input=env_text,
    text=True,
)
assert round_trip == "intel&x86_64_v4"

print("mfc f405 three-stage workflow checks: PASS")
