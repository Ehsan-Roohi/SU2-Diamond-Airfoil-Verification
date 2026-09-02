#!/usr/bin/env python3
"""Static regression gates for the hybrid Reynolds/t31 analysis workflow."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np


root = Path(__file__).resolve().parents[1]
workflow = root / "mfc_iles_a40" / "reynolds_t31_analysis"
sys.path.insert(0, str(workflow))

python_files = (
    workflow / "raw_restart_reader.py",
    workflow / "build_long_view.py",
    workflow / "case_evidence.py",
    workflow / "analyze_pruned_initial.py",
    workflow / "analyze_long_chain.py",
    workflow / "cv_physics_labels.py",
    workflow / "export_cv_dataset.py",
    workflow / "cv_dataset_loader.py",
    workflow / "render_mfc_suite.py",
    workflow / "aggregate_mfc_suite.py",
    root / "mfc_iles_a40" / "hll_production_analysis" / "analyze_mfc_hll_article.py",
)
for path in python_files:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

shell_files = tuple(workflow.glob("*.sh")) + tuple(workflow.glob("*.sbatch"))
for path in shell_files:
    subprocess.run(["bash", "-n", str(path)], check=True)

builder = (workflow / "build_long_view.py").read_text(encoding="utf-8")
for token in (
    "PASS_HYBRID_RETAINED_AND_DERIVED",
    "BYTE_IDENTICAL_RAW",
    "SINGLE_RETAINED_RAW_PLUS_MARKER",
    "DERIVED_HISTORY_PLUS_MARKER",
    "selected_movie_prefix",
    "selected_diagnostics",
):
    assert token in builder, token
assert "missing duplicated boundary" not in builder

submitter = (workflow / "unity_submit_reynolds_t31_analysis.sh").read_text(
    encoding="utf-8"
)
for token in (
    "require_series \"$RE1E4_ROOT/f180\" 121",
    "require_series \"$LADDER_ROOT/re5e4\" 61",
    "--check-only",
    "CASE_EVIDENCE_SCRIPT",
    "LONG_ANALYZER",
    "PRUNED_ANALYZER",
    "CV_DATASET_JOB",
    "run_cv_dataset.sbatch",
    'job-name=mfc-r31-ml',
):
    assert token in submitter, token
assert submitter.index("--check-only") < submitter.index("PREP_JOB=$(sbatch")

case_runner = (workflow / "run_case_analysis.sbatch").read_text(encoding="utf-8")
assert '[[ "$role" == long_baseline ]]' in case_runner
assert '"$LONG_ANALYZER"' in case_runner
assert '"$CASE_EVIDENCE_SCRIPT"' in case_runner

exporter = (workflow / "export_cv_dataset.py").read_text(encoding="utf-8")
for token in (
    'SCHEMA_VERSION = "mfc-cv-physics-v1"',
    "DART_REFERENCE_COMMIT",
    "shock_mask=",
    "shock_ridge=",
    "label_valid_mask=",
    "save_label_rasters",
    "vortex_positive_heatmap=",
    "vortex_negative_heatmap=",
    "vortex_instances=",
    '"guard"',
    '"manifest.jsonl"',
    '"vortex_catalogue.csv"',
    '"shock_catalogue.csv"',
    '"normalization.json"',
    '"dataset_balance.csv"',
    "_stage8_catalogue.csv",
    "SHOCK_GATE_MIN_TIME",
    "normalization_square_sum",
):
    assert token in exporter, token

physics = (workflow / "cv_physics_labels.py").read_text(encoding="utf-8")
for token in ("lambda_ci", "omega_ratio", "gamma2", "q_criterion"):
    assert token in physics, token
assert "research/dart_cfd_pilot" in physics

renderer = (workflow / "render_mfc_suite.py").read_text(encoding="utf-8")
assert "MFC_HLL_T26_T31_SCHLIEREN_VORTICITY.mp4" in renderer
assert "MFC_HLL_T00_T31_SCHLIEREN_VORTICITY.mp4" in renderer
assert "selected_movie_prefix" in renderer
visual_runner = (workflow / "run_visuals.sbatch").read_text(encoding="utf-8")
assert '--ml-dataset "$ANALYSIS_ROOT/ml_dataset"' in visual_runner

aggregate = (workflow / "aggregate_mfc_suite.py").read_text(encoding="utf-8")
assert "SPARSE_RETENTION_NO_SPECTRAL_CLAIM" in aggregate
assert "computer_vision_dataset" in aggregate
assert "PASS_HYBRID_RETAINED_AND_DERIVED" in aggregate

# Small numerical controls protect the label physics and leakage guard logic,
# without requiring any Unity result files in CI.
from cv_physics_labels import (  # noqa: E402
    bow_shock_labels,
    extract_vortex_cores,
    geometry_fluid_mask,
    vortex_diagnostics,
)
from export_cv_dataset import resample, split_sequence  # noqa: E402
from cv_dataset_loader import MFCCVDataset  # noqa: E402

for count in (12, 61, 121):
    splits = split_sequence(count)
    assert len(splits) == count
    assert all(name in splits for name in ("train", "val", "test", "guard"))

x = np.linspace(-1.25, 4.75, 161)
y = np.linspace(-1.25, 4.75, 161)
xx, yy = x[:, None], y[None, :]
fluid = geometry_fluid_mask(x, y)
core_x, core_y = 1.75, 1.05
radius2 = (xx - core_x) ** 2 + (yy - core_y) ** 2
amplitude = np.exp(-radius2 / (2.0 * 0.18**2))
u = -(yy - core_y) * amplitude / 0.18
v = (xx - core_x) * amplitude / 0.18
cores, _thresholds = extract_vortex_cores(
    x, y, vortex_diagnostics(x, y, u, v), fluid
)
assert cores
assert min(
    (row["x_physical"] - core_x) ** 2 + (row["y_physical"] - core_y) ** 2
    for row in cores
) < 0.08**2

alpha = np.deg2rad(40.0)
streamwise = xx * np.cos(alpha) + yy * np.sin(alpha)
normal = -xx * np.sin(alpha) + yy * np.cos(alpha)
synthetic_shock = 80.0 * np.exp(
    -((streamwise + 0.45 + 0.05 * normal) / 0.025) ** 2
)
shock_mask, shock_ridge, shock_info = bow_shock_labels(
    x, y, synthetic_shock, fluid
)
assert shock_info["status"] == "PASS"
assert np.count_nonzero(shock_mask) and np.count_nonzero(shock_ridge)

source_x = np.linspace(0.0, 2.0, 5)
source_y = np.linspace(0.0, 3.0, 7)
source = source_x[:, None] + 2.0 * source_y[None, :]
target_x = np.linspace(0.1, 1.9, 11)
target_y = np.linspace(0.1, 2.9, 13)
sampled = resample(source_x, source_y, source, target_x, target_y)
assert np.allclose(
    sampled, target_x[:, None] + 2.0 * target_y[None, :], atol=1.0e-6
)

with tempfile.TemporaryDirectory() as directory:
    dataset_root = Path(directory)
    (dataset_root / "tensors").mkdir()
    np.savez_compressed(
        dataset_root / "tensors" / "sample.npz",
        fields=np.full((2, 3, 4), 3.0, dtype=np.float32),
        field_names=np.asarray(("a", "b")),
        label_valid_mask=np.asarray(
            [[1, 1, 1, 1], [1, 0, 0, 1], [1, 1, 1, 1]], dtype=np.uint8
        ),
    )
    (dataset_root / "manifest.jsonl").write_text(
        json.dumps({"split": "train", "tensor": "tensors/sample.npz"}) + "\n",
        encoding="utf-8",
    )
    (dataset_root / "normalization.json").write_text(
        json.dumps(
            {
                "channels": {
                    "a": {"mean": 1.0, "std": 2.0},
                    "b": {"mean": 1.0, "std": 2.0},
                }
            }
        ),
        encoding="utf-8",
    )
    loaded = MFCCVDataset(dataset_root, split="train", normalize=True)[0]
    assert np.allclose(loaded["fields"][:, 0, 0], 1.0)
    assert np.allclose(loaded["fields"][:, 1, 1], 0.0)

print("mfc Reynolds/t31 hybrid analysis and ML-export checks: PASS")
