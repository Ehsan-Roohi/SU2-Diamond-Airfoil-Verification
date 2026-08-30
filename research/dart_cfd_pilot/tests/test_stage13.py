import ast
import json
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def test_stage13_python_parses():
    ast.parse((ROOT/"scripts/run_vortex_stage13_calibrated.py").read_text())


def test_stage13_uses_disjoint_temporal_holdout():
    cfg=json.loads((ROOT/"dart_stage13.json").read_text())
    assert cfg["calibration_frame_stop"] < cfg["holdout_frame_start"]
    assert cfg["holdout_frame_stop"] == 60
    assert cfg["minimum_holdout_stage8_coverage"] >= .80
    assert cfg["minimum_holdout_close_member_coverage"] >= .70


def test_stage13_searches_close_core_nms_without_dart():
    cfg=json.loads((ROOT/"dart_stage13.json").read_text())
    assert min(cfg["search_grid"]["nms_radius_factor"]) < 2
    assert min(cfg["search_grid"]["minimum_nms_radius"]) < .08
    source=(ROOT/"scripts/run_vortex_stage13_calibrated.py").read_text()
    assert "detect_deblended" not in source
    assert "close_reference_members" in source


def test_stage13_submit_has_no_patch_marker_arguments():
    source=(ROOT/"scripts/submit_unity_dart_stage13.sh").read_text()
    invocation=source[source.index("PYTHONPATH="):source.index("\ntar -C")]
    assert '"${PYTHON}" +' not in invocation
    assert "SLURM_SUBMIT_DIR" in source
    assert "grep -qx 'status=PASS'" in source
