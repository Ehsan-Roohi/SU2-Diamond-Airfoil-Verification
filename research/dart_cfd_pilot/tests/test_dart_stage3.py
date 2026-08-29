import importlib.util
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_dart_stage3_tracking.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("dart_stage3_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_stage3_config_is_temporal_consensus_gate():
    config = json.loads((ROOT / "dart_stage3.json").read_text())
    assert config["snapshot_dt"] == 0.05
    assert config["minimum_prompt_support"] >= 2
    assert config["minimum_track_frames"] >= 3
    assert config["minimum_qualified_tracks"] >= 3
    assert config["maximum_box_area_fraction"] <= 0.02
    assert config["source_video"].endswith("vorticity-shedding.mp4")
    assert len(config["prompts"]) == len(set(config["prompts"])) == 4


def test_stage3_iou_and_prompt_consensus():
    runner = load_runner()
    assert runner.box_iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    detections = [
        {"prompt": "vortex", "score": 0.4, "box_xyxy": [10, 10, 20, 20]},
        {"prompt": "swirl", "score": 0.3, "box_xyxy": [11, 10, 21, 20]},
        {"prompt": "spiral", "score": 0.2, "box_xyxy": [50, 50, 60, 60]},
    ]
    clusters = runner.cluster_detections(detections, 0.5)
    assert clusters[0]["prompt_support"] == 2
    assert clusters[0]["prompts"] == ["swirl", "vortex"]
    assert clusters[1]["prompt_support"] == 1


def test_stage3_scripts_parse_and_keep_results_shallow():
    compile(RUNNER.read_text(), str(RUNNER), "exec")
    submit = ROOT / "scripts" / "submit_unity_dart_stage3.sh"
    completed = subprocess.run(
        ["bash", "-n", str(submit)], text=True, capture_output=True
    )
    assert completed.returncode == 0, completed.stderr
    script = submit.read_text()
    assert 'readonly OUTPUT_REL="results/${RUN_ID}"' in script
    assert 'results/${RUN_ID}.tar.gz' in script
    assert 'sha256sum "${ARCHIVE}" > "${CHECKSUM}"' in script
    assert "results/inference" not in script
