import ast
import json
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def test_stage14_python_parses():
    ast.parse((ROOT/"scripts/run_vortex_stage14_baselines.py").read_text())


def test_stage14_has_fair_baselines_and_blind_audit():
    source=(ROOT/"scripts/run_vortex_stage14_baselines.py").read_text()
    for method in ['"q"','"lci"','"omega_abs"']:
        assert method in source
    assert "calibrate_method" in source
    assert "stage14_expert_labels.csv" in source
    assert "draw_blind_crop" in source


def test_stage14_predeclares_audit_and_consensus():
    cfg=json.loads((ROOT/"dart_stage14.json").read_text())
    assert cfg["minimum_audit_samples"] >= 20
    assert cfg["audit_samples_per_category"] >= 5
    assert cfg["maximum_consensus_detection_to_reference_ratio"] <= 2
    assert cfg["comparison_frames"] == [30,45,60]


def test_stage14_submit_inputs_and_no_patch_marker():
    source=(ROOT/"scripts/submit_unity_dart_stage14.sh").read_text()
    invocation=source[source.index("PYTHONPATH="):source.index("\ntar -C")]
    assert '"${PYTHON}" +' not in invocation
    assert "stage13_detections.csv" in source
    assert "grep -qx 'status=PASS'" in source
