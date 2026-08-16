#!/usr/bin/env python3
"""Functional regression test for three-grid MFC load recovery."""

from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


root = Path(__file__).resolve().parents[1]
recovery = root / "mfc_grid_convergence" / "recover_a40_grid_convergence.py"
wrapper = root / "mfc_grid_convergence" / "unity_recover_a40_grid_convergence.sh"
levels = {
    "f180": (180, 1944, 48600, 1.0 / 3600.0),
    "f270": (270, 2916, 72900, 1.0 / 5400.0),
    "f405": (405, 4374, 109350, 1.0 / 8100.0),
}

alpha = math.radians(40.0)
q_inf = 4.5

with tempfile.TemporaryDirectory() as temporary:
    project = Path(temporary) / "project"
    output = Path(temporary) / "output"
    for label, (resolution, save_every, final_step, dt) in levels.items():
        restart = project / "mfc_runs" / f"run_{label}" / "case" / "restart_data"
        restart.mkdir(parents=True)
        cd = 0.85 + 50.0 / resolution**2
        cl = 0.95 + 80.0 / resolution**2
        drag = q_inf * cd
        lift = q_inf * cl
        force_x = drag * math.cos(alpha) - lift * math.sin(alpha)
        force_y = drag * math.sin(alpha) + lift * math.cos(alpha)
        for step in range(0, final_step + 1, save_every):
            if step == 0:
                values = (0.0, 0.0, 0.0)
            else:
                values = (step * dt, force_x, force_y)
            record = values + (0.0,) * 17
            (restart / f"ib_state_{step}.dat").write_bytes(
                struct.pack("=20d", *record)
            )

    subprocess.run(
        [
            sys.executable,
            str(recovery),
            "--root",
            str(project),
            "--output-dir",
            str(output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    summary = json.loads(
        (output / "MFC_A40_GRID_CONVERGENCE_SUMMARY.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["recommendation"] == "STOP_AT_F405"
    for metric in ("CD", "CL"):
        result = summary["metrics"][metric]
        assert result["behavior"] == "monotonic_convergence"
        assert math.isclose(result["observed_order"], 2.0, rel_tol=1.0e-10)
        assert result["gci_f405_pct"] < 1.0

    archive = output / "MFC_A40_GRID_CONVERGENCE_RECOVERY.zip"
    with zipfile.ZipFile(archive) as source:
        names = set(source.namelist())
    assert "MFC_A40_F180_FORCE_HISTORY.csv" in names
    assert "MFC_A40_F270_FORCE_HISTORY.csv" in names
    assert "MFC_A40_F405_FORCE_HISTORY.csv" in names
    assert "MFC_A40_GRID_CONVERGENCE.csv" in names
    assert "MFC_A40_GRID_CONVERGENCE_SUMMARY.json" in names
    assert (output / f"{archive.name}.sha256.txt").is_file()

    for step in range(1944, 48600 + 1, 1944):
        path = project / "mfc_runs" / "run_f180" / "case" / "restart_data" / f"ib_state_{step}.dat"
        record = (step / 3600.0, 0.0, 0.0) + (0.0,) * 17
        path.write_bytes(struct.pack("=20d", *record))
    invalid = subprocess.run(
        [
            sys.executable,
            str(recovery),
            "--root",
            str(project),
            "--output-dir",
            str(Path(temporary) / "invalid-output"),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert invalid.returncode != 0
    assert "All-zero histories cannot be used" in invalid.stderr

shell = wrapper.read_text(encoding="utf-8")
assert "recover_a40_grid_convergence.py" in shell
assert "import h5py" in shell
assert "UPLOAD_THIS" not in shell  # The Python reporter owns canonical paths.

print("mfc alpha=40 three-grid convergence checks: PASS")
