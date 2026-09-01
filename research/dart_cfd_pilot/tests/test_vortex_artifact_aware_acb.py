import csv
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_vortex_artifact_aware_acb.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_vortex_artifact_aware_acb", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration():
    return json.loads((ROOT / "vortex_artifact_aware_acb.json").read_text())


def clean_core_features():
    return {
        "wall_distance_cells": 8.0,
        "compression_fraction": 0.05,
        "rotation_purity": 0.8,
        "sign_coherence": 0.9,
        "scale_persistence": 1.0,
        "hessian_compactness": 0.7,
        "ring_valid_fraction": 1.0,
        "ring_coherence": 0.9,
        "radial_to_tangential": 0.2,
    }


def test_clean_compact_core_is_accepted():
    accepted, reason, support, required = load_module().artifact_decision(clean_core_features(), configuration())
    assert accepted
    assert reason == "accepted"
    assert support >= required


def test_wall_staircase_candidate_is_rejected_before_topology_vote():
    features = clean_core_features()
    features["wall_distance_cells"] = 1.0
    accepted, reason, _, _ = load_module().artifact_decision(features, configuration())
    assert not accepted
    assert reason == "wall_mask_proximity"


def test_compressive_shock_bead_is_rejected():
    features = clean_core_features()
    features["compression_fraction"] = 0.9
    accepted, reason, _, _ = load_module().artifact_decision(features, configuration())
    assert not accepted
    assert reason == "compressive_shock_signature"


def test_shear_ridge_without_closed_rotation_is_rejected():
    features = clean_core_features()
    features.update({
        "rotation_purity": 0.0,
        "sign_coherence": 0.3,
        "scale_persistence": 0.0,
        "hessian_compactness": 0.0,
        "ring_coherence": 0.2,
        "radial_to_tangential": 4.0,
    })
    accepted, reason, support, required = load_module().artifact_decision(features, configuration())
    assert not accepted
    assert reason == "insufficient_closed_core_topology"
    assert support < required


def test_boundary_clipped_core_can_pass_without_complete_ring():
    features = clean_core_features()
    features.update({
        "ring_valid_fraction": 0.25,
        "ring_coherence": 0.0,
        "radial_to_tangential": float("inf"),
    })
    accepted, _, support, required = load_module().artifact_decision(features, configuration())
    assert accepted
    assert support >= required


def test_visual_audit_matching_preserves_candidate_identity():
    module = load_module()
    point = {
        "rotation_sign": "1",
        "x_physical": "1.0",
        "y_physical": "2.0",
    }
    exact_candidate = [{"sign": 1, "x": 1.0, "y": 2.0}]
    nearby_distinct_core = [{"sign": 1, "x": 1.05, "y": 2.0}]
    assert module.candidate_identity_survives(point, exact_candidate, 1.0e-9)
    assert not module.candidate_identity_survives(point, nearby_distinct_core, 1.0e-9)


def test_visual_audit_is_complete_and_has_expected_blind_counts():
    labels = list(csv.DictReader((ROOT / "reference" / "acb_cmcd_blind_visual_audit.csv").open()))
    assert len(labels) == 36
    counts = {name: sum(row["is_vortex"] == name for row in labels) for name in ("yes", "no", "uncertain")}
    assert counts == {"yes": 18, "no": 16, "uncertain": 2}
    assert {row["morphology"] for row in labels} == {
        "compact_core", "paired_core", "shear_layer", "shock_or_wall"
    }


def test_configuration_is_predeclared_and_labels_are_posthoc():
    cfg = configuration()
    runner = SCRIPT.read_text()
    submit = (ROOT / "scripts" / "submit_unity_vortex_artifact_aware_acb.sh").read_text()
    assert cfg["method_name"].startswith("Artifact-Aware Adaptive Candidate-Budget")
    assert "visual_audit_informed_feature_design\": True" in runner
    assert "expert_labels_used_for_numeric_threshold_optimization\": False" in runner
    main_body = runner.split("def main() -> int:", 1)[1]
    assert main_body.index("artifact_by_frame: dict") < main_body.index("audit_rows, expert = score_blind_audit(")
    assert "#SBATCH --partition=cpu" in submit
    assert "VORTEX_ARTIFACT_AWARE_ACB_${JOB_ID}_COMPLETE.tar.gz" in submit
    assert "RUN_OK_CCFCV_RAW_FIELDS.txt" in submit
    assert "RUN_OK_RAW_FIELDS.txt" not in submit
    assert "'alpha_deg=30'" in submit
    assert "'final_step=16200'" in submit
    assert cfg["audit_identity_tolerance"] <= 1.0e-8
    assert "expert_match_radius" not in cfg
    assert "exact candidate identity" in runner
