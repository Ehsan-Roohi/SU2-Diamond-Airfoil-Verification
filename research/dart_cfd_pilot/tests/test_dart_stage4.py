import csv
import importlib.util
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_dart_stage4_validation.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("dart_stage4_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def config():
    return json.loads((ROOT / "dart_stage4.json").read_text())


def row(frame, track, x, y, score=0.12, width=20.0, time_step=0.05):
    return {
        "frame_index": str(frame),
        "time": str(frame * time_step),
        "track_id": str(track),
        "score": str(score),
        "prompt_support": "3",
        "prompts": "swirl|vortex",
        "raster_proxy_fraction": "0.4",
        "box_x1": str(x * 100),
        "box_y1": str(y * 100),
        "box_x2": str(x * 100 + width),
        "box_y2": str(y * 100 + width),
        "x_source": "0",
        "y_source": "0",
        "x_physical": str(x),
        "y_physical": str(y),
    }


def test_stage4_deduplicates_overlapping_track_identities():
    runner = load_runner()
    rows = []
    for frame in range(10, 16):
        rows.append(row(frame, 26, 0.1 * frame, 0.05 * frame))
        rows.append(row(frame, 32, 0.1 * frame + 0.02, 0.05 * frame + 0.01))
    summaries, audit = runner.audit_tracks(rows, config())
    assert audit["duplicate_components"] == [[26, 32]]
    assert len(audit["duplicate_pairs"]) == 1
    assert sum(item["duplicate_identity"] for item in summaries) == 1
    assert audit["unique_strictly_qualified_tracks"] == 1


def test_stage4_reference_matching_is_one_to_one():
    runner = load_runner()
    detections = [row(1, 4, 1.0, 1.0), row(1, 5, 2.0, 2.0)]
    references = [
        {"frame_index": "1", "reference_id": "a", "x_physical": "1.02", "y_physical": "1.01"},
        {"frame_index": "1", "reference_id": "b", "x_physical": "2.02", "y_physical": "2.01"},
    ]
    metrics, matches = runner.validate_reference(detections, references, config())
    assert len(matches) == 2
    assert metrics["precision"] == metrics["recall"] == metrics["f1"] == 1.0
    assert metrics["pass"] is True


def test_stage4_cli_blocks_publication_without_reference(tmp_path):
    stage3 = tmp_path / "stage3"
    output = tmp_path / "output"
    stage3.mkdir()
    rows = [row(frame, 6, 0.1 * frame, 0.05 * frame) for frame in range(5, 13)]
    with (stage3 / "stage3_tracks.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (stage3 / "stage3_report.json").write_text(
        json.dumps(
            {
                "case_id": "synthetic",
                "project_commit": "abc",
                "accepted_consensus_detections": 20,
                "temporal_summary": {"qualified_tracks": 4},
                "claim_gate": "temporal_signal_present_needs_raw_field_validation",
            }
        )
    )
    completed = subprocess.run(
        [
            "python",
            str(RUNNER),
            "--stage3-dir",
            str(stage3),
            "--output-dir",
            str(output),
        ],
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads((output / "stage4_report.json").read_text())
    assert report["gates"]["physical_validation"] == "not_run_raw_field_reference_required"
    assert report["gates"]["publication_claim"] == "fail"
    assert report["stage3_frequency_proxy_usable"] is False


def test_stage4_scripts_parse_and_keep_results_shallow():
    compile(RUNNER.read_text(), str(RUNNER), "exec")
    submit = ROOT / "scripts" / "submit_unity_dart_stage4.sh"
    completed = subprocess.run(["bash", "-n", str(submit)], text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    script = submit.read_text()
    assert 'readonly OUTPUT_REL="results/${RUN_ID}"' in script
    assert 'results/${RUN_ID}.tar.gz' in script
    assert "results/inference" not in script
