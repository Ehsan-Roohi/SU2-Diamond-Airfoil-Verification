#!/usr/bin/env python3
"""Static regression checks for compact f405 packaging."""

from __future__ import annotations

import ast
from pathlib import Path


root = Path(__file__).resolve().parents[1]
packer = root / "mfc_grid_convergence" / "pack_f405_results.py"
submitter = root / "mfc_grid_convergence" / "unity_submit_pack_f405.sh"

tree = ast.parse(packer.read_text(encoding="utf-8"))
assignments: dict[str, object] = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            try:
                assignments[target.id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass

assert assignments["NX"] == 4455
assert assignments["NY"] == 4050
assert assignments["FINAL_STEP"] == 109350
assert assignments["SAVE_EVERY"] == 4374

text = packer.read_text(encoding="utf-8")
for token in (
    'default=-1.5',
    'default=5.5',
    'default=-2.5',
    'default=4.5',
    'default=6',
    'np.savez_compressed(',
    '"rho"',
    '"pres"',
    '"vel1"',
    '"vel2"',
    '"ib_mask"',
):
    assert token in text, token

if submitter.exists():
    shell = submitter.read_text(encoding="utf-8")
    for token in (
        "RUN_OK_F405.txt",
        "MFC_A40_F405_MOVIE_READY_",
        "LAST_MFC_A40_F405_PACKAGE.env",
        "silo_hdf5",
        'PACK_MEMORY="${PACK_MEMORY:-16G}"',
        'PACK_WALLTIME="${PACK_WALLTIME:-02:00:00}"',
    ):
        assert token in shell, token

print("mfc f405 compact packaging checks: PASS")
