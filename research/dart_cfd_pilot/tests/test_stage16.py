import ast
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_vortex_stage16_regions.py"


def load_module():
    spec = importlib.util.spec_from_file_location("stage16_test_module", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_stage16_python_parses():
    ast.parse(SCRIPT.read_text())


def test_positive_quantile_ignores_zero_inflation():
    module = load_module()
    field = np.zeros((20, 20))
    field[10:12, 10:12] = [[1.0, 2.0], [3.0, 4.0]]
    threshold = module.positive_quantile_threshold(field, np.ones_like(field, dtype=bool), 0.5)
    assert threshold == 2.5


def test_pure_translation_does_not_change_derived_vorticity():
    module = load_module()
    axis = np.linspace(-0.5, 0.5, 81)
    u, v = module.synthetic_velocity(axis, separation=0.12)
    moved_u, moved_v = module.synthetic_velocity(axis, separation=0.12, translation=(12.0, -4.0))
    dux, duy = np.gradient(u, axis, axis, edge_order=2)
    dvx, dvy = np.gradient(v, axis, axis, edge_order=2)
    moved_dux, moved_duy = np.gradient(moved_u, axis, axis, edge_order=2)
    moved_dvx, moved_dvy = np.gradient(moved_v, axis, axis, edge_order=2)
    assert np.max(np.abs((dvx - duy) - (moved_dvx - moved_duy))) < 1.0e-10
    assert np.max(np.abs(dux - moved_dux)) < 1.0e-10
    assert np.max(np.abs(dvy - moved_dvy)) < 1.0e-10


def test_stage16_configuration_preserves_holdout_and_candidate_control():
    cfg = json.loads((ROOT / "dart_stage16.json").read_text())
    assert cfg["calibration_frames"] == [1, 30]
    assert cfg["holdout_frames"] == [31, 60]
    assert cfg["target_maximum_detection_to_reference_ratio"] == 1.30
    assert max(cfg["calibration_grid"]["nms_radius"]) < cfg["close_pair_maximum_separation"]


def test_submit_is_flat_and_consumes_stage15_gate():
    source = (ROOT / "scripts/submit_unity_dart_stage16.sh").read_text()
    assert 'ARCHIVE="${PROJECT_ROOT}/STAGE16_VORTEX_${RUN_ID}_COMPLETE.tar.gz"' in source
    assert "stage15_report.json" in source
    assert "grep -qx 'status=PASS'" in source
    assert '"${PYTHON}" +' not in source
