import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_dart_stage6_audit.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("dart_stage6_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def config():
    return json.loads((ROOT / "dart_stage6.json").read_text())


def test_exact_physical_field_of_view_mapping():
    runner = load_runner()
    stage3_config = {
        "plot_bounds_normalized": [0.06, 0.06, 0.83, 0.94],
        "physical_xlim": [-1.25, 4.75],
        "physical_ylim": [-1.25, 4.25],
    }
    report = {
        "source_video_size": [1000, 1000],
        "analysis_crop_pixels": [300, 120, 840, 760],
    }
    field = runner.physical_field_of_view(report, stage3_config)
    assert abs(field["xlim"][0] - 0.6201298701) < 1e-9
    assert abs(field["ylim"][0] + 0.125) < 1e-12


def test_reference_definition_requires_all_persistence_criteria():
    runner = load_runner()
    definition = config()["reference_definitions"][1]
    summary = {
        "observations": 5,
        "lifetime": 0.25,
        "displacement": 0.1,
        "continuity": 0.7,
    }
    assert runner.qualifies(summary, definition)
    summary["continuity"] = 0.69
    assert not runner.qualifies(summary, definition)


def test_claim_distinguishes_sparse_precision_from_comprehensive_tracking():
    runner = load_runner()
    cfg = config()
    definitions = [
        {
            "name": item["name"],
            "track_identity_coverage": 0.25,
            "observation_coverage": 0.04,
        }
        for item in cfg["reference_definitions"]
    ]
    claim, classification = runner.classify_claim(0.97, definitions, False, cfg)
    assert claim == "diagnostic_high_precision_sparse_vortex_localization"
    assert classification["sparse_localization"]
    assert not classification["comprehensive_tracking"]
