import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_vortex_analytic_positive_control.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_vortex_analytic_positive_control", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config():
    return json.loads((ROOT / "vortex_analytic_positive_control.json").read_text())


def test_lamb_oseen_has_expected_rotation_sign_and_pressure_minimum():
    module = load_module()
    x = np.linspace(-0.3, 0.3, 101)
    xx, yy = np.meshgrid(x, x, indexing="ij")
    u, v, drop = module.lamb_oseen(xx, yy, 0.0, 0.0, 0.8, 0.08)
    dx = x[1] - x[0]
    omega = np.gradient(v, dx, axis=0) - np.gradient(u, dx, axis=1)
    assert omega[50, 50] > 0.0
    assert drop[50, 50] == np.max(drop)


def test_suite_is_predeclared_and_contains_positive_and_negative_controls():
    module = load_module()
    cfg = config()
    cases = module.case_definitions(cfg)
    categories = {row["category"] for row in cases}
    assert {"isolated", "co_pair", "counter_pair", "shock_vortex", "negative"} <= categories
    assert cfg["future_case_recalibration_allowed"] is False
    assert min(cfg["close_pair_separations"]) < cfg["base_physics_configuration"]["nms_radius"]


def test_matching_requires_correct_rotation_sign():
    module = load_module()
    truth = [{"x": 0.0, "y": 0.0, "sign": 1}]
    wrong = [{"x": 0.0, "y": 0.0, "sign": -1}]
    right = [{"x": 0.01, "y": 0.0, "sign": 1}]
    assert module.score(truth, wrong, 0.05)["true_positive"] == 0
    assert module.score(truth, right, 0.05)["true_positive"] == 1


def test_unity_archive_is_flat_and_not_named_stage():
    submit_path = ROOT / "scripts" / "submit_unity_vortex_analytic_positive_control.sh"
    submit = submit_path.read_text()
    assert "${PROJECT_ROOT}/VORTEX_ANALYTIC_PC_" in submit
    assert "PYTHONNOUSERSITE=1" in submit
    assert "stage" not in submit_path.name
