import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_vortex_cylinder_wake_validation.py"
SRA_RUNNER = ROOT / "scripts" / "run_vortex_shock_ridge_aware_su2.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_lbm_solver_compiles_and_writes_smoke_sequence(tmp_path):
    source = ROOT / "scripts" / "cylinder_lbm_d2q9.c"
    binary = tmp_path / "cylinder_lbm_d2q9"
    subprocess.run(
        [
            "cc", "-O2", "-fopenmp", "-std=c11", "-Wall", "-Wextra",
            "-Werror", str(source), "-lm", "-o", str(binary),
        ],
        check=True,
    )
    output = tmp_path / "smoke"
    subprocess.run(
        [str(binary), "64", "48", "8", "100", "0.08", "2", "0", "1", "1", str(output)],
        check=True,
        timeout=30,
    )
    assert len(list(output.glob("snapshot_*.bin"))) == 3
    assert (output / "cylinder_monitor.csv").is_file()


def test_lbm_solver_supports_square_obstacle_without_breaking_legacy_cli(tmp_path):
    source = ROOT / "scripts" / "cylinder_lbm_d2q9.c"
    binary = tmp_path / "cylinder_lbm_d2q9"
    subprocess.run(
        [
            "cc", "-O2", "-fopenmp", "-std=c11", "-Wall", "-Wextra",
            "-Werror", str(source), "-lm", "-o", str(binary),
        ],
        check=True,
    )
    output = tmp_path / "square_smoke"
    completed = subprocess.run(
        [
            str(binary), "64", "48", "8", "100", "0.08", "2", "0", "1", "1",
            str(output), "square",
        ],
        check=True,
        timeout=30,
        text=True,
        capture_output=True,
    )
    assert "LBM_OBSTACLE_SHAPE=square" in completed.stdout
    assert len(list(output.glob("snapshot_*.bin"))) == 3


def test_square_obstacle_mask_has_flat_faces_and_declared_side_length():
    pytest.importorskip("scipy")
    runner = load_module("test_square_geometry", RUNNER)
    cfg = {"solver": {
        "nx": 64,
        "ny": 48,
        "diameter_cells": 8,
        "cylinder_x_diameters_from_inlet": 5.0,
        "obstacle_shape": "square",
    }}
    x, y, fluid = runner.cylinder_coordinates(cfg)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    solid = ~fluid
    assert np.all(np.abs(xx[solid]) <= 0.5)
    assert np.all(np.abs(yy[solid]) <= 0.5)
    assert np.any(solid & (np.abs(xx) > 0.40) & (np.abs(yy) > 0.40))


def test_gamma2_reference_finds_a_lamb_oseen_core():
    pytest.importorskip("scipy")
    runner = load_module("test_cylinder_runner", RUNNER)
    x = np.linspace(-1.0, 1.0, 101)
    xx, yy = np.meshgrid(x, x, indexing="ij")
    radius_squared = xx * xx + yy * yy
    circulation = 1.0
    core_radius = 0.15
    factor = circulation * (1.0 - np.exp(-radius_squared / core_radius**2))
    factor /= 2.0 * math.pi * np.maximum(radius_squared, 1.0e-20)
    u = -factor * yy
    v = factor * xx
    fluid = np.ones_like(u, dtype=bool)
    gamma2 = runner.gamma2_field(u, v, fluid, radius=4)
    peak = np.unravel_index(np.nanargmax(gamma2), gamma2.shape)
    assert abs(x[peak[0]]) <= 0.04
    assert abs(x[peak[1]]) <= 0.04
    assert gamma2[peak] >= 2.0 / math.pi


def test_gamma2_reference_censors_components_clipped_by_evaluation_roi():
    pytest.importorskip("scipy")
    runner = load_module("test_cylinder_reference_censor", RUNNER)
    x = np.linspace(-1.0, 1.0, 21)
    gamma2 = np.zeros((21, 21), dtype=float)
    omega = np.ones_like(gamma2)
    fluid = np.ones_like(gamma2, dtype=bool)
    # One interior island and one island cut by the x=0.5 ROI boundary.
    gamma2[9:12, 9:12] = 0.9
    gamma2[15:17, 3:7] = 0.9
    cfg = {"evaluation": {
        "gamma2_threshold": 0.6,
        "gamma2_minimum_component_cells": 4,
        "gamma2_radius_cells": 1,
        "wake_x_over_d": [-0.5, 0.5],
        "wake_y_over_d": [-0.8, 0.8],
        "reference_boundary_margin_cells": 1,
        "reference_roi_boundary_censoring": True,
    }}
    audit = {}
    rows = runner.reference_centers(gamma2, omega, x, x, fluid, cfg, audit=audit)
    assert len(rows) == 1
    assert abs(rows[0]["x"]) <= 0.11
    assert audit["roi_boundary_censored_components"] == 1


def test_scoring_roi_censors_detector_centers_symmetrically():
    pytest.importorskip("scipy")
    runner = load_module("test_cylinder_scoring_roi", RUNNER)
    cfg = {"evaluation": {
        "wake_x_over_d": [1.0, 14.0],
        "wake_y_over_d": [-3.0, 3.0],
    }}
    detections = [
        {"x": 2.0, "y": 0.0},
        {"x": 1.0, "y": 0.0},
        {"x": 14.2, "y": 0.0},
        {"x": 2.0, "y": 3.0},
    ]
    kept, censored = runner.scoring_detections(detections, cfg)
    assert kept == [detections[0]]
    assert censored == 3


def test_scale_adaptive_pressure_law_preserves_frozen_default():
    sra = load_module("test_cylinder_sra", SRA_RUNNER)
    pressure = {"ring_support": 2, "offset_cells": 3.0, "pass": False}
    island = {"area_cells": 144.0}
    frozen = {
        "maximum_pressure_minimum_offset_cells": 2.0,
        "minimum_pressure_ring_support": 2,
    }
    unchanged = sra.scale_adaptive_pressure_support(pressure, island, frozen)
    assert unchanged["allowed_offset_cells"] == 2.0
    assert unchanged["pass"] is False

    adaptive = dict(frozen)
    adaptive.update({
        "pressure_offset_equivalent_radius_fraction": 0.5,
        "maximum_scale_adaptive_pressure_offset_cells": 5.0,
    })
    accepted = sra.scale_adaptive_pressure_support(pressure, island, adaptive)
    assert accepted["allowed_offset_cells"] > 2.0
    assert accepted["pass"] is True


def test_development_and_holdout_protocols_are_distinct_and_frozen():
    development = json.loads((ROOT / "vortex_cylinder_wake_validation.json").read_text())
    holdout = json.loads((ROOT / "vortex_cylinder_wake_re150_holdout.json").read_text())
    adaptive = json.loads((ROOT / "vortex_scale_adaptive_sra_cmcd.json").read_text())
    assert development["solver"]["reynolds_number"] == 100.0
    assert holdout["solver"]["reynolds_number"] == 150.0
    assert development["case_id"] != holdout["case_id"]
    assert holdout["frozen_detector_sources"]["detector_recalibration_allowed"] is False
    assert adaptive["future_case_recalibration_allowed"] is False
    assert "independent" in holdout["case_role"]
    assert "holdout" in holdout["case_role"]


def test_unity_archive_is_flat_and_not_named_stage():
    submit = ROOT / "scripts" / "submit_unity_vortex_cylinder_wake_validation.sh"
    text = submit.read_text()
    assert "${PROJECT_ROOT}/VORTEX_CYLINDER_WAKE_" in text
    assert "PYTHONNOUSERSITE=1" in text
    assert "stage" not in submit.name


def test_cross_geometry_protocol_is_frozen_and_submission_is_flat():
    protocol = json.loads(
        (ROOT / "vortex_square_cylinder_re150_cross_geometry_holdout.json").read_text()
    )
    sensitivity = json.loads(
        (ROOT / "vortex_square_cylinder_re150_blockage_sensitivity.json").read_text()
    )
    assert protocol["solver"]["obstacle_shape"] == "square"
    assert protocol["validation_role"] == "independent_holdout"
    assert protocol["frequency_gate_provenance"]["frozen_before_first_execution"] is True
    assert protocol["frozen_detector_sources"]["detector_recalibration_allowed"] is False
    assert sensitivity["validation_role"] == "diagnostic_sensitivity"
    assert sensitivity["diagnostic_provenance"]["holdout_result_remains_failed"] is True
    submit = ROOT / "scripts" / "submit_unity_vortex_cross_geometry_validation.sh"
    text = submit.read_text()
    assert "${PROJECT_ROOT}/VORTEX_TSA_SRA_CMCD_CROSS_GEOMETRY_" in text
    assert "HOLDOUT_PROTOCOL_FREEZE_COMMIT" in text
    assert "PYTHONNOUSERSITE=1" in text
    assert "stage" not in submit.name


def test_square_re100_prospective_protocol_is_frozen_and_flat():
    protocol = json.loads(
        (ROOT / "vortex_square_cylinder_re100_prospective_holdout.json").read_text()
    )
    assert protocol["solver"]["obstacle_shape"] == "square"
    assert protocol["solver"]["reynolds_number"] == 100.0
    assert protocol["solver"]["blockage_ratio"] == 0.05
    assert protocol["validation_role"] == "prospective_holdout"
    assert protocol["acceptance_gates"]["strouhal_number_range"] == [0.155, 0.175]
    assert protocol["frequency_gate_provenance"]["frozen_before_first_Re100_execution"] is True
    assert protocol["frozen_detector_sources"]["detector_recalibration_allowed"] is False
    assert protocol["frozen_detector_sources"]["local_preexecution_protocol_freeze_commit"] == (
        "cf98501f6e79c7052a6ad48e9f9e8e680744d265"
    )
    submit = ROOT / "scripts" / "submit_unity_vortex_square_re100_prospective.sh"
    text = submit.read_text()
    assert "${PROJECT_ROOT}/VORTEX_TSA_SRA_CMCD_SQUARE_RE100_" in text
    assert "PYTHONNOUSERSITE=1" in text
    assert "PUBLISHED_PROTOCOL_RECORD_COMMIT" in text
    assert "stage" not in submit.name


def test_square_re120_v2_holdout_is_frozen_before_execution():
    temporal = json.loads(
        (ROOT / "vortex_temporal_wide_window_tsa_sra_cmcd_v2.json").read_text()
    )
    protocol = json.loads(
        (ROOT / "vortex_square_cylinder_re120_v2_holdout.json").read_text()
    )
    assert temporal["lookaround_frames"] == 4
    assert temporal["maximum_convection_speed_over_u_infinity"] == 1.30
    assert temporal["future_case_recalibration_allowed"] is False
    assert temporal["gamma2_used_by_detector"] is False
    assert protocol["validation_role"] == "prospective_holdout"
    assert protocol["solver"]["reynolds_number"] == 120.0
    assert protocol["acceptance_gates"]["strouhal_number_range"] == [0.145, 0.175]
    assert protocol["frequency_gate_provenance"]["frozen_before_first_Re120_execution"] is True
    assert protocol["frozen_detector_sources"]["detector_recalibration_allowed"] is False
    assert protocol["frozen_detector_sources"]["local_preexecution_v2_and_holdout_freeze_commit"] == (
        "0b895f34e05e0c7f990a8ed1a551c4755713dc1c"
    )
    submit = ROOT / "scripts" / "submit_unity_vortex_square_re120_v2_holdout.sh"
    text = submit.read_text()
    assert "${PROJECT_ROOT}/VORTEX_TSA_SRA_CMCD_V2_SQUARE_RE120_" in text
    assert "LOCAL_PREEXECUTION_FREEZE_COMMIT" in text
    assert "PUBLISHED_PROTOCOL_RECORD_COMMIT" in text
    assert "stage" not in submit.name
