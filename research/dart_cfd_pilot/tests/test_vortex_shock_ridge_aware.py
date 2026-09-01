import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_vortex_shock_ridge_aware_su2.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_vortex_shock_ridge_aware_su2", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration():
    return json.loads((ROOT / "vortex_shock_ridge_aware_cmcd.json").read_text())


def candidate():
    return {"x": 0.0, "y": 0.0, "sign": 1, "grid_i": 50, "grid_j": 50}


def test_multiradius_winding_accepts_solid_body_rotation_and_rejects_shear():
    pytest.importorskip("scipy", reason="SRA-CMCD numerical tests require SciPy")
    module = load_module()
    cfg = configuration()
    x = np.linspace(-1.0, 1.0, 101)
    y = np.linspace(-1.0, 1.0, 101)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    fluid = np.ones_like(xx, dtype=bool)
    vortex = {"x": x, "y": y, "u": -yy, "v": xx, "fluid": fluid}
    shear = {"x": x, "y": y, "u": yy, "v": np.zeros_like(xx), "fluid": fluid}
    vortex_features = module.ring_winding_features(vortex, candidate(), 8.0, 64)
    shear_features = module.ring_winding_features(shear, candidate(), 8.0, 64)
    assert module.winding_pass(vortex_features, cfg)
    assert not module.winding_pass(shear_features, cfg)
    assert vortex_features["signed_winding"] == pytest.approx(1.0)


def test_absolute_thermodynamic_ridge_mask_reports_distance_in_cells():
    pytest.importorskip("scipy", reason="SRA-CMCD numerical tests require SciPy")
    module = load_module()
    cfg = configuration()
    shape = (21, 21)
    pressure_jump = np.zeros(shape)
    entropy_jump = np.zeros(shape)
    pressure_jump[:, 10] = 2.0 * cfg["minimum_pressure_jump_per_cell"]
    entropy_jump[:, 10] = 2.0 * cfg["minimum_entropy_jump_per_cell"]
    snapshot = {
        "fluid": np.ones(shape, dtype=bool),
        "pressure_jump_sensor": pressure_jump,
        "entropy_jump_sensor": entropy_jump,
    }
    mask, distance = module.build_shock_ridge_mask(snapshot, cfg)
    assert mask[:, 10].all()
    assert distance[10, 13] == pytest.approx(3.0)


def test_shock_ridge_veto_is_hard_and_fail_closed():
    module = load_module()
    cfg = configuration()
    island = {"pass": True}
    pressure = {"pass": True}
    accepted, reason = module.revised_decision(
        island, cfg["minimum_winding_ring_support"], pressure,
        cfg["maximum_shock_ridge_distance_cells"] + 1.0, cfg,
    )
    assert accepted and reason == "accepted"
    accepted, reason = module.revised_decision(
        island, cfg["minimum_winding_ring_support"], pressure,
        cfg["maximum_shock_ridge_distance_cells"] - 1.0, cfg,
    )
    assert not accepted
    assert reason == "thermodynamic_shock_ridge_proximity"


def test_pressure_corroboration_precedes_shock_distance():
    module = load_module()
    cfg = configuration()
    accepted, reason = module.revised_decision(
        {"pass": True}, cfg["minimum_winding_ring_support"], {"pass": False},
        cfg["maximum_shock_ridge_distance_cells"] - 1.0, cfg,
    )
    assert not accepted
    assert reason == "pressure_minimum_not_corroborated"


def test_protocol_names_development_case_and_flat_root_archive():
    cfg = configuration()
    submit = (ROOT / "scripts" / "submit_unity_vortex_shock_ridge_aware.sh").read_text()
    runner = SCRIPT.read_text()
    assert cfg["method_name"].startswith("Shock-Ridge-Aware")
    assert "development_negative_control" in cfg["case_id"]
    assert cfg["threshold_provenance"]["future_case_recalibration_allowed"] is False
    assert "never independent validation" in runner
    assert "VORTEX_SHOCK_RIDGE_CMCD_${JOB_ID}_COMPLETE.tar.gz" in submit
    assert "${PROJECT_ROOT}/VORTEX_SHOCK_RIDGE_CMCD_" in submit
