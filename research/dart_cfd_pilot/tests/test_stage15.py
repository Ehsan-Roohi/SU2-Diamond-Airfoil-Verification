import ast
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_vortex_stage15_vgcm.py"


def load_module():
    spec = importlib.util.spec_from_file_location("stage15_test_module", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_stage15_python_parses():
    ast.parse(SCRIPT.read_text())


def test_stage15_predeclares_vgcm_and_holdout():
    cfg = json.loads((ROOT / "dart_stage15.json").read_text())
    assert cfg["gamma_kernel_sizes"] == [5, 7, 9, 11]
    assert cfg["gamma1_minimum"] == 0.63
    assert cfg["calibration_frames"] == [1, 30]
    assert cfg["holdout_frames"] == [31, 60]


def test_gamma2_is_galilean_invariant_and_localizes_core():
    module = load_module()
    result = module.synthetic_invariance_check()
    assert result["pass"]
    assert result["translation_maximum_difference"] <= 1.0e-10
    assert result["core_location_error"] <= 0.05


def test_gamma2_rejects_pure_translation():
    module = load_module()
    axis = np.linspace(-1.0, 1.0, 31)
    u = np.full((31, 31), 8.0)
    v = np.full((31, 31), -3.0)
    fluid = np.ones_like(u, dtype=bool)
    _, gamma2 = module.gamma_pair(axis, axis, u, v, fluid, 4)
    assert np.nanmax(np.abs(gamma2[5:-5, 5:-5])) < 1.0e-8


def test_variable_gamma_resolves_close_same_sign_cores():
    module = load_module()
    axis = np.linspace(-0.5, 0.5, 121)
    xx, yy = np.meshgrid(axis, axis, indexing="ij")
    u = np.zeros_like(xx)
    v = np.zeros_like(xx)
    for center_x in (-0.08, 0.08):
        dx = xx - center_x
        radius2 = dx * dx + yy * yy
        factor = (1.0 - np.exp(-radius2 / 0.0016)) / (radius2 + 1.0e-12)
        u -= yy * factor
        v += dx * factor
    fluid = np.ones_like(u, dtype=bool)
    gamma = module.variable_gamma(axis, axis, u + 20.0, v - 3.0, fluid, [5, 7, 9, 11], 0.63)
    candidates = module.raw_candidates(
        axis,
        axis,
        gamma["gamma2"],
        gamma["gamma2_support"],
        gamma["gamma2_scale"],
        gamma["radii"],
        fluid,
        0.63,
        2,
        method="gi_vgcm",
    )
    accepted = module.nms(candidates, 0.04, 10)
    assert len(accepted) == 2
    assert accepted[0]["sign"] == accepted[1]["sign"] == 1
    assert sorted(round(row["x"], 2) for row in accepted) == [-0.08, 0.08]


def test_stage15_submit_has_all_inputs_and_no_literal_patch_marker():
    source = (ROOT / "scripts/submit_unity_dart_stage15.sh").read_text()
    invocation = source[source.index("PYTHONPATH="):source.index("\ntar -C")]
    assert '"${PYTHON}" +' not in invocation
    assert "stage13_detections.csv" in source
    assert "stage14_report.json" in source
    assert "grep -qx 'status=PASS'" in source
