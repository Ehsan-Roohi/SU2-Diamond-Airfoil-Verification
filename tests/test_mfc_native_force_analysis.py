from __future__ import annotations

import importlib.util
import math
import struct
import sys
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1]
    / "mfc_iles_a40/reynolds_t31_analysis/native_force_analysis/extract_native_ib_forces.py"
)
if not MODULE_PATH.is_file():
    MODULE_PATH = Path(__file__).parent / "native_force_analysis/extract_native_ib_forces.py"
SPEC = importlib.util.spec_from_file_location("extract_native_ib_forces", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_rotation_and_normalization() -> None:
    alpha = math.radians(MODULE.ALPHA_DEG)
    expected_cd = 0.82
    expected_cl = 1.07
    drag = expected_cd * MODULE.Q_INF
    lift = expected_cl * MODULE.Q_INF
    force_x = drag * math.cos(alpha) - lift * math.sin(alpha)
    force_y = drag * math.sin(alpha) + lift * math.cos(alpha)
    _, _, cd, cl = MODULE.force_coefficients(force_x, force_y)
    assert math.isclose(cd, expected_cd, rel_tol=1.0e-13)
    assert math.isclose(cl, expected_cl, rel_tol=1.0e-13)


def test_global_record_contract(tmp_path: Path) -> None:
    path = tmp_path / "ib_state_42.dat"
    values = [4.2, 1.0, 2.0, 3.0] + [0.0] * 16
    path.write_bytes(struct.pack("=20d", *values))
    records = MODULE.read_global_ib_records(path)
    assert len(records) == 1
    assert records[0][:4] == (4.2, 1.0, 2.0, 3.0)


def test_mismatched_boundary_is_not_equivalent() -> None:
    left = {"time": 6.0, "force_x": 1.0, "force_y": 2.0, "force_z": 0.0}
    right = dict(left)
    assert MODULE.records_equivalent(left, right)
    right["force_y"] += 1.0e-4
    assert not MODULE.records_equivalent(left, right)


def test_full_synthetic_workflow() -> None:
    MODULE.self_test()
