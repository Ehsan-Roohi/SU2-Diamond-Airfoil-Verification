import ast
import json
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def test_stage12_python_parses():
    ast.parse((ROOT/"scripts/run_vortex_stage12_mfc_persistent.py").read_text())


def test_stage12_predeclares_higher_cap_and_temporal_gate():
    cfg=json.loads((ROOT/"dart_stage12.json").read_text())
    assert 40 < cfg["maximum_detections_per_frame"] <= 120
    assert cfg["minimum_track_observations"] >= 3
    assert 0 < cfg["minimum_track_continuity"] <= 1
    assert cfg["minimum_persistent_stage8_coverage"] >= .70


def test_stage10c_cap_is_configurable_and_backward_compatible():
    source=(ROOT/"scripts/run_vortex_stage10c_absolute_scale.py").read_text()
    assert "cfg.get('maximum_detections',40)" in source
    assert "len(accepted)>=maximum_detections" in source


def test_stage12_submit_is_spool_safe_and_uses_completed_raw_fields():
    source=(ROOT/"scripts/submit_unity_dart_stage12.sh").read_text()
    assert "SLURM_SUBMIT_DIR" in source
    assert "grep -qx 'status=PASS'" in source
    assert "module load" not in source
