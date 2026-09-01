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
    clockwise = {"x": x, "y": y, "u": yy, "v": -xx, "fluid": fluid}
    clockwise_candidate = {**candidate(), "sign": -1}
    clockwise_features = module.ring_winding_features(clockwise, clockwise_candidate, 8.0, 64)
    assert module.winding_pass(clockwise_features, cfg)
    assert vortex_features["absolute_winding"] == pytest.approx(1.0)
    assert clockwise_features["absolute_winding"] == pytest.approx(1.0)


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


def test_q_island_window_expands_past_artificial_edge_but_not_domain_edge():
    pytest.importorskip("scipy", reason="SRA-CMCD numerical tests require SciPy")
    module = load_module()
    cfg = configuration()
    ii, jj = np.meshgrid(np.arange(101), np.arange(101), indexing="ij")
    q = (((ii - 50) ** 2 + (jj - 50) ** 2) <= 20**2).astype(float)
    displaced = {**candidate(), "grid_j": 42}
    island = module.closed_q_island({"q": q}, displaced, cfg)
    assert island["pass"]
    assert island["analysis_radius_cells"] > cfg["q_island_radius_cells"]

    open_ridge = np.zeros_like(q)
    open_ridge[48:53, :] = 1.0
    ridge = module.closed_q_island({"q": open_ridge}, candidate(), cfg)
    assert not ridge["pass"]
    assert not ridge["closed"]


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


def test_pressure_minimum_must_be_collocated_with_rotation_center():
    pytest.importorskip("scipy", reason="SRA-CMCD numerical tests require SciPy")
    module = load_module()
    cfg = configuration()
    x = np.linspace(-0.5, 0.5, 51)
    xx, yy = np.meshgrid(x, x, indexing="ij")
    snapshot = {"x": x, "y": x, "pressure": 1.0 + xx * xx + yy * yy}
    collocated = module.pressure_core_support(
        snapshot, {"grid_i": 25, "grid_j": 25}, cfg,
    )
    displaced = module.pressure_core_support(
        snapshot, {"grid_i": 28, "grid_j": 28}, cfg,
    )
    assert collocated["pass"]
    assert collocated["offset_cells"] == 0.0
    assert not displaced["pass"]
    assert displaced["offset_cells"] == pytest.approx(3.0 * np.sqrt(2.0))


def test_subordinate_same_sign_peak_is_removed_but_opposite_sign_pair_is_kept():
    module = load_module()
    cfg = configuration()
    strong = {
        "grid_i": 50, "grid_j": 50, "sign": 1, "score": 100.0,
        "pressure_core": {"offset_cells": 0.0}, "accepted": True,
        "rejection_reason": "accepted",
    }
    satellite = {
        "grid_i": 56, "grid_j": 50, "sign": 1, "score": 40.0,
        "pressure_core": {"offset_cells": 3.0}, "accepted": True,
        "rejection_reason": "accepted",
    }
    opposite = {
        "grid_i": 56, "grid_j": 50, "sign": -1, "score": 40.0,
        "pressure_core": {"offset_cells": 3.0}, "accepted": True,
        "rejection_reason": "accepted",
    }
    module.suppress_subordinate_same_sign_peaks([strong, satellite, opposite], cfg)
    assert not satellite["accepted"]
    assert satellite["rejection_reason"] == "subordinate_same_sign_peak"
    assert opposite["accepted"]


def test_pressure_displacement_exception_requires_corroborated_opposite_sign_pair():
    module = load_module()
    cfg = configuration()

    def row(sign, grid_i):
        return {
            "grid_i": grid_i, "grid_j": 50, "sign": sign, "score": 100.0,
            "q_island": {"pass": True}, "winding_support": 3,
            "pressure_core": {"ring_support": 3, "offset_cells": 3.0},
            "shock_ridge_distance_cells": 20.0, "accepted": False,
            "rejection_reason": "pressure_minimum_not_corroborated",
        }

    positive = row(1, 44)
    negative = row(-1, 56)
    module.rescue_corroborated_opposite_sign_pairs([positive, negative], cfg)
    assert positive["accepted"] and negative["accepted"]

    isolated = row(1, 50)
    module.rescue_corroborated_opposite_sign_pairs([isolated], cfg)
    assert not isolated["accepted"]


def test_protocol_names_development_case_and_flat_root_archive():
    cfg = configuration()
    submit = (ROOT / "scripts" / "submit_unity_vortex_shock_ridge_aware.sh").read_text()
    requirements = (ROOT / "requirements-shock-ridge-aware.txt").read_text()
    runner = SCRIPT.read_text()
    assert cfg["method_name"].startswith("Shock-Ridge-Aware")
    assert "unlabelled_diagnostic" in cfg["case_id"]
    assert cfg["threshold_provenance"]["future_case_recalibration_allowed"] is False
    assert "neither a zero-vortex" in runner
    assert "nor an independent validation case" in runner
    assert "VORTEX_SHOCK_RIDGE_CMCD_${JOB_ID}_COMPLETE.tar.gz" in submit
    assert "${PROJECT_ROOT}/VORTEX_SHOCK_RIDGE_CMCD_" in submit
    assert "PYTHONNOUSERSITE=1" in submit
    assert "requirements-shock-ridge-aware.txt" in submit
    assert "matplotlib==3.10.8" in requirements
    assert "numpy==1.26.4" in requirements
