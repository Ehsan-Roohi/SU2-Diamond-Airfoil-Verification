import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_vortex_acb_cmcd.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_vortex_acb_cmcd", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidates(scores):
    return [
        {"x": float(index), "y": 0.0, "sign": 1, "score": float(score), "method": "q"}
        for index, score in enumerate(scores)
    ]


def test_score_elbow_selects_physical_break_after_minimum():
    module = load_module()
    scores = [100.0 - index for index in range(36)] + [12.0 - 0.05 * index for index in range(30)]
    selected, diagnostics = module.select_adaptive(candidates(scores), {
        "minimum_detections": 30,
        "maximum_detections": 64,
        "minimum_log_score_gap": 0.4,
        "tail_score_fraction": 0.5,
    })
    assert len(selected) == 36
    assert diagnostics["selection_reason"] == "score_elbow"


def test_tail_support_can_expand_beyond_legacy_cap():
    module = load_module()
    scores = [100.0 - index for index in range(60)]
    selected, diagnostics = module.select_adaptive(candidates(scores), {
        "minimum_detections": 30,
        "maximum_detections": 64,
        "minimum_log_score_gap": 2.0,
        "tail_score_fraction": 0.75,
    })
    assert len(selected) > 30
    assert diagnostics["selection_reason"] == "tail_support"


def test_config_uses_technical_name_and_temporal_holdout():
    cfg = json.loads((ROOT / "vortex_acb_cmcd.json").read_text())
    assert cfg["method_name"].startswith("Adaptive Candidate-Budget")
    assert "Stage" not in cfg["method_name"]
    assert cfg["calibration_frames"][1] < cfg["temporal_holdout_frames"][0]
    assert max(cfg["selector_grid"]["maximum_detections"]) > 30


def test_submit_is_cpu_only_and_writes_flat_archive():
    submit = (ROOT / "scripts" / "submit_unity_vortex_acb_cmcd.sh").read_text()
    assert "#SBATCH --partition=cpu" in submit
    assert "--gres=gpu" not in submit
    assert "VORTEX_ACB_CMCD_${RUN_ID}_COMPLETE.tar.gz" in submit
    assert "${PROJECT_ROOT}/VORTEX_ACB_CMCD_" in submit
