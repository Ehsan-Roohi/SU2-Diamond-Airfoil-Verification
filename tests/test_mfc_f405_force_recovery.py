#!/usr/bin/env python3
"""Functional regression test for f405 IB-force recovery."""

from __future__ import annotations

import csv
import json
import math
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


root = Path(__file__).resolve().parents[1]
extractor = root / "mfc_grid_convergence" / "extract_f405_ib_forces.py"

alpha = math.radians(40.0)
drag = 4.5
lift = 9.0
force_x = drag * math.cos(alpha) - lift * math.sin(alpha)
force_y = drag * math.sin(alpha) + lift * math.cos(alpha)

with tempfile.TemporaryDirectory() as temporary:
    case_dir = Path(temporary) / "case"
    restart_dir = case_dir / "restart_data"
    output_dir = Path(temporary) / "output"
    restart_dir.mkdir(parents=True)

    samples = (
        (0, 0.0, 0.0, 0.0),
        (69984, 8.64, force_x, force_y),
        (109350, 13.5, force_x, force_y),
    )
    for step, time, fx, fy in samples:
        record = (time, fx, fy) + (0.0,) * 17
        (restart_dir / f"ib_state_{step}.dat").write_bytes(
            struct.pack("=20d", *record)
        )

    subprocess.run(
        [
            sys.executable,
            str(extractor),
            str(case_dir),
            "--output-dir",
            str(output_dir),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    csv_path = output_dir / "MFC_A40_F405_FORCE_HISTORY.csv"
    with csv_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3
    assert math.isclose(float(rows[-1]["CD"]), 1.0, rel_tol=1.0e-12)
    assert math.isclose(float(rows[-1]["CL"]), 2.0, rel_tol=1.0e-12)

    summary = json.loads(
        (output_dir / "MFC_A40_F405_FORCE_SUMMARY.json").read_text(
            encoding="utf-8"
        )
    )
    late = summary["late_time_t_ge_8p64"]
    assert late["samples"] == 2
    assert math.isclose(late["CD_mean"], 1.0, rel_tol=1.0e-12)
    assert math.isclose(late["CL_mean"], 2.0, rel_tol=1.0e-12)

print("mfc f405 force recovery checks: PASS")
