import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_low_count_calibration_options_remove_legacy_floor():
    cfg = json.loads((ROOT / "vortex_fc_acb_cmcd.json").read_text())
    minimums = cfg["selector_grid"]["minimum_detections"]
    assert min(minimums) < 30
    assert max(minimums) < 30
    assert cfg["calibration_frames"][1] < cfg["temporal_holdout_frames"][0]


def test_claim_requires_calibration_candidate_control():
    runner = (ROOT / "scripts" / "run_vortex_acb_cmcd.py").read_text()
    assert '"calibration_candidate_control"' in runner
    assert 'calibration_metrics["feasible"]' in runner


def test_technical_method_name_and_flat_cpu_archive():
    cfg = json.loads((ROOT / "vortex_fc_acb_cmcd.json").read_text())
    submit = (ROOT / "scripts" / "submit_unity_vortex_fc_acb_cmcd.sh").read_text()
    assert "Stage" not in cfg["method_name"]
    assert "Feasibility-Constrained" in cfg["method_name"]
    assert "#SBATCH --partition=cpu" in submit
    assert "--gres=gpu" not in submit
    assert "${PROJECT_ROOT}/VORTEX_FC_ACB_CMCD_" in submit
