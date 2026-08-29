import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def test_case_images_exist_and_are_readable():
    config = json.loads((ROOT / "dart_cases.json").read_text())
    assert len(config["prompts"]) == 4
    for case in config["cases"]:
        assert len(case.get("prompts", config["prompts"])) <= 4
        path = ROOT / case["image"]
        assert path.is_file() and path.stat().st_size > 10_000
        with Image.open(path) as image:
            assert image.width >= 800
            assert image.height >= 600


def test_primary_pair_is_mach_and_incidence_matched():
    config = json.loads((ROOT / "dart_cases.json").read_text())
    euler = next(case for case in config["cases"] if case["id"] == "euler_mfc_alpha40_mach")
    viscous = next(case for case in config["cases"] if case["id"] == "viscous_mfc_alpha40_schlieren_t3")
    assert euler["mach"] == viscous["mach"] == 3.0
    assert euler["alpha_deg"] == viscous["alpha_deg"] == 40.0
    assert euler["solver"] == "MFC"
    assert viscous["solver"].startswith("MFC commit")


def test_preflight_does_not_claim_inference():
    report = json.loads((ROOT / "results" / "dart_preflight_2026-08-29.json").read_text())
    assert report["status"] == "blocked_external_dependency"
    inference = next(check for check in report["checks"] if check["name"] == "cfd_inference")
    assert inference["status"] == "not_run"


def test_runner_records_all_cases_with_a_fake_dart_cli(tmp_path):
    dart_repo = tmp_path / "DART"
    dart_repo.mkdir()
    fake_demo = dart_repo / "demo_multiclass.py"
    fake_demo.write_text(
        "from pathlib import Path\n"
        "import argparse\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--image')\n"
        "p.add_argument('--classes', nargs='+')\n"
        "p.add_argument('--checkpoint')\n"
        "p.add_argument('--device')\n"
        "p.add_argument('--imgsz')\n"
        "p.add_argument('--confidence')\n"
        "p.add_argument('--detection-only', action='store_true')\n"
        "p.add_argument('--output')\n"
        "a=p.parse_args()\n"
        "Path(a.output).write_bytes(b'fake-image')\n"
    )
    checkpoint = tmp_path / "sam3.pt"
    checkpoint.write_bytes(b"fake-checkpoint")
    output_dir = tmp_path / "outputs"
    runner = ROOT / "scripts" / "run_dart_pilot.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--dart-repo",
            str(dart_repo),
            "--checkpoint",
            str(checkpoint),
            "--device",
            "cpu",
            "--imgsz",
            "504",
            "--output-dir",
            str(output_dir),
        ],
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads((output_dir / "dart_run_report.json").read_text())
    config = json.loads((ROOT / "dart_cases.json").read_text())
    assert report["status"] == "completed"
    assert report["detection_only"] is False
    assert len(report["runs"]) == len(config["cases"])


def test_unity_submit_uses_slurm_submit_directory():
    script = (ROOT / "scripts" / "submit_unity_dart_pilot.sh").read_text()
    assert "SLURM_SUBMIT_DIR" in script
    assert "DART_PROJECT_ROOT" in script
    assert 'readonly PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"' not in script


def test_unity_submit_pins_pkg_resources_compatible_setuptools():
    script = (ROOT / "scripts" / "submit_unity_dart_pilot.sh").read_text()
    assert 'readonly SETUPTOOLS_VERSION="81.0.0"' in script
    assert '"setuptools==${SETUPTOOLS_VERSION}"' in script
    assert "if ! python -c 'import pkg_resources'" in script
