import importlib.util
import sys
from pathlib import Path

import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_vortex_stage10c_absolute_scale.py"


def load_module():
    spec = importlib.util.spec_from_file_location("stage10c", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["stage10c"] = module
    spec.loader.exec_module(module)
    return module


def test_isolated_vortex_is_detected_with_correct_sign():
    m = load_module()
    x = np.linspace(-1, 1, 161)
    y = np.linspace(-0.8, 0.8, 129)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    truth = [m.Vortex(0.1, -0.1, -1.0, 0.14)]
    u, v = m.add_lamb_oseen(xx, yy, truth)
    cfg = {
        "scales": [1.0, 2.0, 4.0, 8.0, 12.0],
        "minimum_lambda_ci": 1.0,
        "minimum_absolute_gamma2": 0.8,
        "minimum_omega_ratio": 0.7,
        "minimum_lci_snr": 6.0,
        "rotation_floor_factor": 2.5,
        "minimum_sign_coherence": 0.65,
        "gamma_window_factor": 2.0,
        "nms_radius_factor": 2.0,
        "minimum_nms_radius": 0.08,
        "analysis_boundary_margin": 0.1,
    }
    detections = m.detect(x, y, u, v, cfg)
    tp, fp, fn, distance = m.match(detections, truth)
    assert (tp, fp, fn) == (1, 0, 0)
    assert distance[0] <= 0.25


def test_merger_family_is_separate_from_resolved_pair_family():
    m = load_module()
    x = np.linspace(-1, 1, 41)
    y = np.linspace(-0.8, 0.8, 33)
    names = [row[0] for row in m.specs(16, 20260906, x, y)]
    assert "close_resolved" in names
    assert "merger" in names
