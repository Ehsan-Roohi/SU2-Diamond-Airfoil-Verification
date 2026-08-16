#!/usr/bin/env python3
"""Regression and scientific-equivalence checks for the f608 Unity chain."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path


root = Path(__file__).resolve().parents[1]
case_path = root / "mfc_grid_convergence" / "case_f608_restartable.py"
f405_path = root / "mfc_grid_convergence" / "case_f405_restartable.py"
submit_path = root / "mfc_grid_convergence" / "unity_submit_f608_chain.sh"

segments = (
    (0, 32805),
    (32805, 65610),
    (65610, 98415),
    (98415, 131220),
    (131220, 164025),
)
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
                "6561",
            ],
            text=True,
        )
    )
    assert case["m"] + 1 == 6682
    assert case["n"] + 1 == 6075
    assert math.isclose(case["dt"], 1.0 / 12150.0, abs_tol=1.0e-15)
    assert case["t_step_start"] == start
    assert case["t_step_stop"] == stop
    assert case["t_step_save"] == 6561
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

assert [stop / 12150 for _, stop in segments] == [2.7, 5.4, 8.1, 10.8, 13.5]
assert math.isclose(6561 / 12150, 0.54)
dx = 11.0 / 6682
dy = 10.0 / 6075
assert math.isclose(dy, 1.0 / 607.5, abs_tol=1.0e-15)
assert math.isclose(dx, dy, rel_tol=1.0e-4)

# The production f608 case must differ from f405 only in spatial resolution,
# stable CFL-scaled time step, and proportional segment/save indices.
f405 = json.loads(
    subprocess.check_output(
        [
            sys.executable,
            str(f405_path),
            "--start-step",
            "0",
            "--stop-step",
            "4374",
            "--save-every",
            "4374",
        ],
        text=True,
    )
)
f608 = json.loads(
    subprocess.check_output(
        [
            sys.executable,
            str(case_path),
            "--start-step",
            "0",
            "--stop-step",
            "6561",
            "--save-every",
            "6561",
        ],
        text=True,
    )
)
allowed_differences = {"m", "n", "dt", "t_step_stop", "t_step_save"}
assert set(f405) == set(f608)
for key in f405:
    if key not in allowed_differences:
        assert f608[key] == f405[key], key

invalid = subprocess.run(
    [
        sys.executable,
        str(case_path),
        "--start-step",
        "1",
        "--stop-step",
        "32805",
        "--save-every",
        "6561",
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
assert invalid.returncode != 0
assert "segment boundaries must be divisible" in invalid.stderr

submit = submit_path.read_text(encoding="utf-8")
required_tokens = (
    'MEMORY="${MEMORY:-120G}"',
    'WALLTIME="${WALLTIME:-2-00:00:00}"',
    'NTASKS="${NTASKS:-48}"',
    'QOS="${QOS:-}"',
    'CONSTRAINT="${CONSTRAINT:-intel&x86_64_v4}"',
    'AFTER_JOB="${AFTER_JOB:-none}"',
    "SEGMENT_STARTS=(0 32805 65610 98415 131220)",
    "SEGMENT_STOPS=(32805 65610 98415 131220 164025)",
    '--dependency="afterok:${previous_job}"',
    '--dependency="afterany:${AFTER_JOB}"',
    "restart_data/lustre_${START_STEP}.dat",
    "restart_data/lustre_${STOP_STEP}.dat",
    "RUN_OK_F608.txt",
    "F608_FORCE_GATE=PASS",
    "Number of 2D model boundary edges: *4",
    "--no-build",
    'exec flock -s -w 7200 "$LOCK_FILE" bash "$0"',
    '[[ "${MFC_LOCK_HELD:-0}" != 1 ]]',
    "printf '%s=%q\\n'",
    'write_env CONSTRAINT "$CONSTRAINT"',
    "EXPECTED_MFC_COMMIT=0c9a1d434410175ac483b8d71646455444e3b7eb",
    "EXPECTED_CASE_SHA256=f524813a1c8a9b22757f15a5ca945e08fc4d72ae9d4f2b2a11d1d0de782f3ca4",
    "EXPECTED_STL_SHA256=65ea8cb922a7c092df652f630cc16904fc4920c0559ad7eb8664918ea7d6f210",
    'common_args+=(--qos="$QOS")',
)
for token in required_tokens:
    assert token in submit, token

assert "--qos=default" not in submit
assert "flock -s -w 7200 9" not in submit
assert "CONSTRAINT=$CONSTRAINT" not in submit

# submission.env must remain source-safe for Slurm feature expressions.
env_text = subprocess.check_output(
    ["bash", "-c", "printf 'CONSTRAINT=%q\\n' 'intel&x86_64_v4'"], text=True
)
round_trip = subprocess.check_output(
    ["bash", "-c", 'source /dev/stdin; printf %s "$CONSTRAINT"'],
    input=env_text,
    text=True,
)
assert round_trip == "intel&x86_64_v4"

print("mfc f608 five-stage workflow checks: PASS")
