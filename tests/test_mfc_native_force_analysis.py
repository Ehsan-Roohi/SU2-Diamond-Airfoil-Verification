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


def test_unused_3d_nan_is_valid_for_2d_force(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    restart = case_dir / "restart_data"
    restart.mkdir(parents=True)
    values = [0.0, 1.0, 2.0] + [float("nan")] * 17
    (restart / "ib_state_0.dat").write_bytes(struct.pack("=20d", *values))
    values = [1.0, 1.1, 2.1] + [float("nan")] * 17
    (restart / "ib_state_1.dat").write_bytes(struct.pack("=20d", *values))
    source = MODULE.SourceSpec(
        "re1e4_f180", 1.0e4, "f180", "test", "t00_t01",
        case_dir, 1.0, 0, 1, 1, 0
    )
    inventory = MODULE.scan_source(source)
    assert inventory.status == "VALID"
    assert inventory.records[1]["force_z"] == ""
    assert inventory.records[1]["unused_nonfinite_fields"] == 17


def test_mismatched_boundary_is_not_equivalent() -> None:
    left = {"time": 6.0, "force_x": 1.0, "force_y": 2.0, "force_z": 0.0}
    right = dict(left)
    assert MODULE.records_equivalent(left, right)
    right["force_y"] += 1.0e-4
    assert not MODULE.records_equivalent(left, right)


def test_full_synthetic_workflow() -> None:
    MODULE.self_test()
