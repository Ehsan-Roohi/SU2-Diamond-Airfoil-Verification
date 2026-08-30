import importlib.util
import json
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_dart_stage8_physics_catalogue.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("stage8", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_solid_body_rotation_has_positive_core_diagnostics():
    m = load_runner()
    x = np.linspace(-1, 1, 41); y = np.linspace(-1, 1, 41)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    fields = m.diagnostics(x, y, -yy, xx, gamma_radius=2)
    core = (20, 20)
    assert fields["omega"][core] > 1.9
    assert fields["lambda_ci"][core] > 0.9
    assert fields["q"][core] > 0.9
    assert fields["omega_ratio"][core] > 0.49
    assert abs(fields["gamma2"][core]) > 0.95


def test_tracker_preserves_sign_and_uses_velocity_prediction():
    m = load_runner()
    cfg = {"maximum_track_gap_frames": 2, "maximum_reference_displacement": 0.3, "strength_continuity_weight": 0.1}
    tracks = {}; next_id = 1
    def core(x, sign=1):
        return {"x_physical":x,"y_physical":0.0,"rotation_sign":sign,"omega":10.0*sign,"lambda_ci":2.0,"q":1.0,"omega_ratio":0.8,"gamma2":0.9*sign,"criterion_support":5,"confidence":0.9}
    rows, tracks, next_id, _ = m.associate_cores([core(0.0)], 0, tracks, next_id, cfg)
    first = rows[0]["reference_id"]
    rows, tracks, next_id, _ = m.associate_cores([core(0.1)], 1, tracks, next_id, cfg)
    assert rows[0]["reference_id"] == first
    rows, tracks, next_id, _ = m.associate_cores([core(0.3)], 3, tracks, next_id, cfg)
    assert rows[0]["reference_id"] == first
    rows, tracks, next_id, _ = m.associate_cores([core(0.4, -1)], 4, tracks, next_id, cfg)
    assert rows[0]["reference_id"] != first


def test_config_and_submit_are_reproducible():
    cfg = json.loads((ROOT / "dart_stage8.json").read_text())
    assert len(range(cfg["step_start"], cfg["step_stop"] + 1, cfg["step_stride"])) == 61
    assert cfg["minimum_criterion_support"] >= 3
    compile(RUNNER.read_text(), str(RUNNER), "exec")
    submit = ROOT / "scripts" / "submit_unity_dart_stage8.sh"
    result = subprocess.run(["bash", "-n", str(submit)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    text = submit.read_text()
    assert "RUN_OK_RAW_FIELDS.txt" in text
    assert "mfc.sh run" not in text
    assert 'readonly OUTPUT_REL="results/${RUN_ID}"' in text
