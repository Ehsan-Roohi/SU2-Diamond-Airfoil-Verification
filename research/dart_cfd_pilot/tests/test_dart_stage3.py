import importlib.util
import json
import subprocess
import zipfile
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


def test_stage3_resolves_moved_video_and_archive(tmp_path):
    runner = load_runner()
    configured = tmp_path / "old" / "vorticity-shedding.mp4"
    moved = tmp_path / "data" / "new" / configured.name
    moved.parent.mkdir(parents=True)
    moved.write_bytes(b"video")
    resolved, method = runner.resolve_source_video(
        configured, search_roots=[tmp_path / "data"]
    )
    assert resolved == moved.resolve()
    assert method == "discovered_video"

    moved.unlink()
    archive = tmp_path / "data" / "movie-products.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(f"movie_products/{configured.name}", b"archived-video")
    resolved, method = runner.resolve_source_video(
        configured,
        explicit_archive=archive,
        cache_dir=tmp_path / "cache",
    )
    assert resolved.read_bytes() == b"archived-video"
    assert method.startswith("extracted_archive:")


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
    assert "DART_STAGE3_VIDEO" in script
    assert "DART_STAGE3_ARCHIVE" in script
    assert "results/inference" not in script
