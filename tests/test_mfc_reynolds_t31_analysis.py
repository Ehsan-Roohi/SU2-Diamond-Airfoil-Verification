#!/usr/bin/env python3
"""Static regression gates for the hybrid Reynolds/t31 analysis workflow."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np


root = Path(__file__).resolve().parents[1]
workflow = root / "mfc_iles_a40" / "reynolds_t31_analysis"
sys.path.insert(0, str(workflow))

python_files = (
    workflow / "raw_restart_reader.py",
    workflow / "build_cv_raw_view.py",
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
    "NONIDENTICAL_RAW_PLUS_CHAIN_PROVENANCE",
    "SINGLE_RETAINED_RAW_PLUS_MARKER",
    "DERIVED_HISTORY_PLUS_MARKER",
    "selected_movie_prefix",
    "selected_diagnostics",
):
    assert token in builder, token
assert "missing duplicated boundary" not in builder
assert "raw restart discontinuity" not in builder

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

cv_submitter = (workflow / "unity_submit_cv_dataset.sh").read_text(
    encoding="utf-8"
)
for token in (
    "build_cv_raw_view.py",
    "--check-only",
    "job-name=mfc-cv-data",
    "MFC_CV_DATASET_SUBMITTED=PASS",
    "TRAINING_DATASET=",
):
    assert token in cv_submitter, token
assert "PREP_JOB" not in cv_submitter
assert "ANALYSIS_JOB" not in cv_submitter

cv_view_builder = (workflow / "build_cv_raw_view.py").read_text(
    encoding="utf-8"
)
assert "PRECEDING_STAGE_FINAL_IS_CANONICAL_AT_DUPLICATED_BOUNDARY" in cv_view_builder
assert "chosen.setdefault" in cv_view_builder

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
assert "NONIDENTICAL_RAW_PLUS_CHAIN_PROVENANCE" in aggregate

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
import build_cv_raw_view  # noqa: E402
import build_long_view  # noqa: E402

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

# A restart stage can rewrite its copied start-step output.  Verify that a
# nonidentical duplicate is recorded (not rejected) only after exact chain
# provenance has been validated, and that the preceding final state remains
# the canonical field selected for the hybrid view.
with tempfile.TemporaryDirectory() as directory:
    base = Path(directory)
    initial = base / "initial"
    chain = base / "chain"
    initial.mkdir()
    (initial / "RUN_OK_INITIAL.txt").write_text("status=PASS\n", encoding="utf-8")
    source_rows = [("t00_t06", 0.0, 6.0, initial)]
    for label, beginning, ending in build_long_view.STAGES:
        stage = chain / label
        stage.mkdir(parents=True)
        left = source_rows[-1]
        start_step = round(beginning / build_long_view.DT)
        stop_step = round(ending / build_long_view.DT)
        (stage / "RUN_OK_RESTART.txt").write_text(
            f"status=PASS\nstart_step={start_step}\nstop_step={stop_step}\n",
            encoding="utf-8",
        )
        (stage / "stage.env").write_text(
            f"STAGE={label}\nSOURCE_DIR={left[3]}\nCASE_DIR={stage}\n"
            f"START_TIME={beginning:g}\nSTOP_TIME={ending:g}\n"
            f"START_STEP={start_step}\nSTOP_STEP={stop_step}\n",
            encoding="utf-8",
        )
        source_rows.append((label, beginning, ending, stage))

    def fake_path(label: str, step: int) -> Path:
        return chain / label / "restart_data" / f"lustre_{step}.dat"

    dense_tail = range(140400, 167400 + 1, 270)
    field_maps = {
        str(initial.resolve()): {32400: initial / "restart_data/lustre_32400.dat"},
        str((chain / "t06_t11").resolve()): {},
        str((chain / "t11_t16").resolve()): {},
        str((chain / "t16_t21").resolve()): {
            113400: fake_path("t16_t21", 113400)
        },
        str((chain / "t21_t26").resolve()): {
            step: fake_path("t21_t26", step)
            for step in range(113400, 140400 + 1, 2700)
        },
        str((chain / "t26_t31").resolve()): {
            step: fake_path("t26_t31", step) for step in dense_tail
        },
    }
    # Ensure both copies of t=26 are represented as well.
    field_maps[str((chain / "t21_t26").resolve())][140400] = fake_path(
        "t21_t26", 140400
    )

    def fake_discovery(path: Path) -> dict[int, Path]:
        return field_maps[str(Path(path).resolve())]

    diagnostics = [{
        "force": {"path": str(base / "force.csv"), "time_end": 21.0},
        "shock": {"path": str(base / "shock.csv"), "time_end": 21.0},
        "mtime": 1.0,
    }]
    movies = [{
        "path": str(base / "MFC_HLL_T0_T26_SCHLIEREN_VORTICITY.mp4"),
        "time_start": 0.0,
        "time_end": 26.0,
        "bytes": 2_000_000,
        "mtime": 1.0,
    }]

    with (
        mock.patch.object(build_long_view, "discover_raw_fields", side_effect=fake_discovery),
        mock.patch.object(build_long_view, "diagnostic_sources", return_value=diagnostics),
        mock.patch.object(build_long_view, "movie_sources", return_value=movies),
        mock.patch.object(
            build_long_view,
            "digest",
            side_effect=lambda path: f"sha:{Path(path).resolve()}",
        ),
    ):
        report = build_long_view.inventory(initial, chain)

    by_step = {row["step"]: row for row in report["boundary_identity"]}
    assert by_step[113400]["audit_status"] == "NONIDENTICAL_RAW_PLUS_CHAIN_PROVENANCE"
    assert by_step[113400]["stage_provenance"]["valid"] is True
    assert report["all_fields"][113400][0] == "t16_t21"
    assert report["all_fields"][140400][0] == "t21_t26"

# The CV-only source view must work without diagnostics or movies and must
# retain the canonical preceding-stage copy at duplicated restart steps.
with tempfile.TemporaryDirectory() as directory:
    base = Path(directory)
    initial = base / "initial"
    chain = base / "chain"
    (initial / "restart_data").mkdir(parents=True)
    (initial / "RUN_OK_INITIAL.txt").write_text("status=PASS\n", encoding="utf-8")
    for grid_name in ("lustre_x_cb.dat", "lustre_y_cb.dat"):
        (initial / "restart_data" / grid_name).write_bytes(b"grid")

    cv_maps: dict[str, dict[int, Path]] = {}

    def create_fields(case: Path, steps: list[int]) -> dict[int, Path]:
        restart = case / "restart_data"
        restart.mkdir(parents=True, exist_ok=True)
        result: dict[int, Path] = {}
        for step in steps:
            path = restart / f"lustre_{step}.dat"
            path.write_bytes(f"{case.name}:{step}".encode())
            result[step] = path.resolve()
        cv_maps[str(case.resolve())] = result
        return result

    create_fields(initial, [32400])
    for label, beginning, ending in build_cv_raw_view.STAGES:
        stage = chain / label
        stage.mkdir(parents=True)
        (stage / "RUN_OK_RESTART.txt").write_text(
            f"status=PASS\nstart_step={round(beginning / build_cv_raw_view.DT)}\n",
            encoding="utf-8",
        )
        if label == "t16_t21":
            steps = [113400]
        elif label == "t21_t26":
            steps = list(range(113400, 140400 + 1, 2700))
        elif label == "t26_t31":
            steps = list(range(140400, 167400 + 1, 270))
        else:
            steps = []
        create_fields(stage, steps)

    with mock.patch.object(
        build_cv_raw_view,
        "discover_raw_fields",
        side_effect=lambda path: cv_maps[str(Path(path).resolve())],
    ):
        cv_report = build_cv_raw_view.build(initial, chain, base / "view")

    assert cv_report["status"] == "PASS"
    assert cv_report["dense_t26_t31_fields"] == 101
    assert cv_report["retained_unique_fields"] >= 111
    assert (base / "view/restart_data/lustre_113400.dat").resolve() == (
        chain / "t16_t21/restart_data/lustre_113400.dat"
    ).resolve()

print("mfc Reynolds/t31 hybrid analysis and ML-export checks: PASS")
