import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_materializer():
    path = ROOT / "scripts/materialize_mfc_cross_case.py"
    spec = importlib.util.spec_from_file_location("materialize_mfc_cross_case", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_materializer_changes_only_pinned_angle_assignment():
    module = load_materializer()
    source = '#!/usr/bin/env python3\n"""Post-process and restart the MFC A40 viscous/no-model screen safely."""\nalpha_deg = 40.0\n'
    rendered = module.materialize(source, 30.0)
    assert "alpha_deg = 30.0" in rendered
    assert "alpha_deg = 40.0" not in rendered
    assert "Cross-case MFC viscous/no-model" in rendered


def test_ccfcv_is_frozen_and_cross_case():
    cfg = json.loads((ROOT / "vortex_ccfcv_alpha30.json").read_text())
    assert cfg["alpha_deg"] == 30.0
    assert cfg["evaluation_frames"] == [1, 60]
    assert cfg["minimum_source_metric_retention"] == 0.80
    runner = (ROOT / "scripts/run_vortex_ccfcv.py").read_text()
    assert "calibrate_method(" not in runner
    assert "calibration_grid" not in runner
    assert 'source_report["selected_baseline_configurations"]["q"]' in runner


def test_ccfcv_submit_has_flat_archive_and_fail_closed_inputs():
    source = (ROOT / "scripts/submit_unity_vortex_ccfcv.sh").read_text()
    assert 'ARCHIVE="${PROJECT_ROOT}/VORTEX_CCFCV_ALPHA30_${RUN_ID}_COMPLETE.tar.gz"' in source
    assert "RUN_OK_CCFCV_RAW_FIELDS.txt" in source
    assert "partial CC-FCV run exists" in source
    assert "CCFCV_SCIENTIFIC_RC=" in source
    assert '"${analysis_rc}" -eq 0 || "${analysis_rc}" -eq 8' in source
    assert '"${MFC_PYTHON}" +' not in source


def test_ccfcv_recovery_never_repeats_the_expensive_simulation():
    source = (ROOT / "scripts/submit_unity_vortex_ccfcv_recover.sh").read_text()
    assert "recovered_from_job=63811016" in source
    assert "verify_binary_sequence" in source
    assert "CCFCV_POSTPROCESS_RECOVERY=completed" in source
    assert "RUN_OK_CCFCV_RAW_FIELDS.txt" in source
    assert "-t post_process" in source
    assert "-t simulation" not in source
    assert "-t pre_process" not in source
