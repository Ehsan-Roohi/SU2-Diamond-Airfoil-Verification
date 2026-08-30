import ast
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def test_stage11_script_parses_and_freezes_deblend_gate():
    path=ROOT/"scripts/run_vortex_stage11_mfc.py"
    tree=ast.parse(path.read_text())
    assert tree is not None
    text=path.read_text()
    assert '"minimum_bic_gain":10000.' in text
    assert '"minimum_improvement":.95' in text


def test_stage11_submit_uses_completed_raw_case():
    text=(ROOT/"scripts/submit_unity_dart_stage11.sh").read_text()
    assert "RUN_OK_RAW_FIELDS.txt" in text
    assert "stage8_catalogue.csv" in text
    assert "--stage8-catalogue" in text
