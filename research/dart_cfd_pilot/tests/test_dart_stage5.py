import importlib.util
import json
import subprocess
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_dart_stage5_raw_reference.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("dart_stage5_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def config():
    return json.loads((ROOT / "dart_stage5.json").read_text())


def test_solid_body_rotation_has_expected_vorticity_and_swirling_strength():
    runner = load_runner()
    x = np.linspace(-1.0, 1.0, 41)
    y = np.linspace(-1.0, 1.0, 41)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    vel1 = -yy
    vel2 = xx
    omega, lambda_ci = runner.velocity_diagnostics(x, y, vel1, vel2)
    assert np.allclose(omega[2:-2, 2:-2], 2.0, atol=1e-12)
    assert np.allclose(lambda_ci[2:-2, 2:-2], 1.0, atol=1e-12)


def test_core_extraction_suppresses_nearby_pixels():
    runner = load_runner()
    x = np.linspace(0.0, 1.0, 101)
    y = np.linspace(0.0, 1.0, 101)
    omega = np.zeros((101, 101))
    swirling = np.zeros_like(omega)
    for i, j, value in ((20, 20, 10.0), (21, 21, 9.0), (80, 80, 8.0)):
        omega[i, j] = value
        swirling[i, j] = value
    cfg = config()
    cfg["lambda_ci_quantile"] = 0.0
    cfg["absolute_vorticity_quantile"] = 0.0
    cfg["minimum_core_separation"] = 0.08
    cores, _ = runner.extract_cores(
        x, y, omega, swirling, np.ones_like(omega, dtype=bool), cfg
    )
    assert len(cores) == 2


def test_temporal_association_preserves_signed_identity():
    runner = load_runner()
    cfg = config()
    first = [
        {"x_physical": 0.0, "y_physical": 0.0, "omega": 2.0, "lambda_ci": 1.0, "rotation_sign": 1},
        {"x_physical": 1.0, "y_physical": 0.0, "omega": -2.0, "lambda_ci": 1.0, "rotation_sign": -1},
    ]
    rows1, active, next_id = runner.associate_cores(first, 0, {}, 1, cfg)
    second = [
        {"x_physical": 0.1, "y_physical": 0.0, "omega": 2.0, "lambda_ci": 1.0, "rotation_sign": 1},
        {"x_physical": 0.9, "y_physical": 0.0, "omega": -2.0, "lambda_ci": 1.0, "rotation_sign": -1},
    ]
    rows2, _, _ = runner.associate_cores(second, 1, active, next_id, cfg)
    assert [row["reference_id"] for row in rows1] == [row["reference_id"] for row in rows2]


def test_stage5_config_and_submit_script_are_reproducible():
    cfg = config()
    assert cfg["step_stop"] == 16200
    assert cfg["step_stride"] == 270
    assert len(range(cfg["step_start"], cfg["step_stop"] + 1, cfg["step_stride"])) == 61
    compile(RUNNER.read_text(), str(RUNNER), "exec")
    submit = ROOT / "scripts" / "submit_unity_dart_stage5_regenerate.sh"
    completed = subprocess.run(["bash", "-n", str(submit)], text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    script = submit.read_text()
    assert "6f71c45d1223dab62dc8f65b1f05dc369ab5932e" in script
    assert "0c9a1d434410175ac483b8d71646455444e3b7eb" in script
    assert 'readonly OUTPUT_REL="results/${RUN_ID}"' in script
    assert "make_recovery_movies.py" not in script
