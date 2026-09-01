import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_vortex_mfc_su2_cross_solver_audit.py"


def load_module():
    spec = importlib.util.spec_from_file_location("vortex_mfc_su2_cross_solver_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cross_solver_protocol_is_frozen_and_retrospective():
    cfg = json.loads((ROOT / "vortex_mfc_su2_cross_solver_audit.json").read_text())
    assert cfg["future_case_recalibration_allowed"] is False
    assert cfg["frozen_sources"]["detector_recalibration_allowed"] is False
    assert "retrospective" in cfg["case_role"]
    assert "Stage-8 physics catalogue" in cfg["reference"]["criterion"]
    assert cfg["mfc"]["step_stop"] == 16200
    assert len(cfg["su2"]["restart_members"]) == 2


def test_matching_is_spatial_and_rotation_sign_is_a_separate_metric():
    module = load_module()
    reference = [{"x": 0.0, "y": 0.0, "sign": 1}]
    detections = [
        {"x": 0.02, "y": 0.0, "sign": -1},
        {"x": 1.0, "y": 1.0, "sign": 1},
    ]
    metrics = module.score_frame(reference, detections, 0.08)
    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 1
    assert metrics["false_negative"] == 0
    assert metrics["correct_rotation_sign"] == 0
    assert metrics["unmatched_detection_indices"] == [1]


def test_airfoil_geometry_can_override_cylinder_wall_rule():
    temporal_path = ROOT / "scripts" / "temporal_vortex_recovery.py"
    spec = importlib.util.spec_from_file_location("cross_solver_temporal_override", temporal_path)
    temporal = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = temporal
    spec.loader.exec_module(temporal)
    cfg = json.loads((ROOT / "vortex_temporal_wide_window_tsa_sra_cmcd_v2.json").read_text())
    protocol = {"solver": {"inlet_lattice_velocity": 0.1, "diameter_cells": 1.0}}

    def accepted(x):
        return {"x": x, "y": 0.0, "sign": 1}

    candidate = {
        "x": 2.0, "y": 0.0, "sign": 1, "accepted": False,
        "rejection_reason": "pressure_minimum_not_corroborated",
        "q_island_pass": True, "winding_support": 3,
        "pressure_core": {"ring_support": 3}, "outside_wall": False,
    }
    records = [
        {"step": 0, "base_detections": [accepted(1.8)], "detections": [accepted(1.8)], "runtime": {"audit": []}},
        {"step": 1, "base_detections": [], "detections": [], "runtime": {"audit": [candidate]}},
        {"step": 2, "base_detections": [accepted(2.2)], "detections": [accepted(2.2)], "runtime": {"audit": []}},
    ]
    audit = temporal.recover(records, cfg, protocol)
    assert audit[0]["outside_wall"] is False
    assert audit[0]["temporally_recovered"] is False


def test_unity_archive_is_flat_and_named_by_method_scope():
    submit = ROOT / "scripts" / "submit_unity_vortex_mfc_su2_cross_solver.sh"
    text = submit.read_text()
    assert '${PROJECT_ROOT}/VORTEX_MFC_SU2_CROSS_SOLVER_' in text
    assert "--solver mfc" in text
    assert "--solver su2" in text
    assert "--reference-catalogue" in text
    assert "PYTHONNOUSERSITE=1" in text
