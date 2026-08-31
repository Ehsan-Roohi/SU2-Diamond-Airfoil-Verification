#!/usr/bin/env python3
"""Cross-case validation of a frozen CMCD/Q configuration."""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def load_sibling(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def draw_physical(path: Path, snapshot: dict, detections: list[dict]) -> None:
    import matplotlib.pyplot as plt

    masked = np.where(snapshot["fluid"], snapshot["omega"], np.nan)
    limit = max(float(np.nanpercentile(np.abs(masked), 99.5)), 1.0e-8)
    levels = np.linspace(-limit, limit, 81)
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.2), sharex=True, sharey=True, constrained_layout=True)
    labels = ["(a) Raw vorticity", "(b) Criteria reference", "(c) Frozen CMCD/Q"]
    for axis, label in zip(axes, labels):
        axis.contourf(
            snapshot["x"], snapshot["y"], masked.T,
            levels=levels, cmap="RdBu_r", extend="both"
        )
        axis.set_title(label)
        axis.set_aspect("equal")
        axis.set_xlabel("x/c")
    axes[0].set_ylabel("y/c")
    reference = snapshot["reference"]
    if reference:
        axes[1].scatter(
            [float(row["x_physical"]) for row in reference],
            [float(row["y_physical"]) for row in reference],
            marker="+", s=42, c="black", linewidths=1.25, label="reference core"
        )
        axes[1].legend(loc="upper left", frameon=True, fontsize=8)
    if detections:
        axes[2].scatter(
            [row["x"] for row in detections], [row["y"] for row in detections],
            s=58, facecolors="none", edgecolors="#00bde3", linewidths=1.6,
            label="frozen CMCD/Q"
        )
        axes[2].legend(loc="upper left", frameon=True, fontsize=8)
    fig.savefig(path, dpi=260, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    reference_tools = load_sibling("ccfcv_reference_tools", "run_dart_stage5_raw_reference.py")
    detector_tools = load_sibling("ccfcv_detector_tools", "run_vortex_stage14_baselines.py")
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--mfc-root", type=Path, required=True)
    parser.add_argument("--source-baseline-report", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads((args.config or ROOT / "vortex_ccfcv_alpha30.json").read_text())
    source_report = json.loads(args.source_baseline_report.read_text())
    frozen = source_report["selected_baseline_configurations"]["q"]
    source_metrics = source_report["holdout_metrics"]["q"]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    case_dir = args.case_dir.resolve()
    mfc_root = args.mfc_root.resolve()
    sys.path.insert(0, str(mfc_root / "toolchain"))
    from mfc.viz.reader import assemble, discover_timesteps

    required = list(range(cfg["step_start"], cfg["step_stop"] + 1, cfg["step_stride"]))
    available = discover_timesteps(str(case_dir), "binary")
    missing = sorted(set(required) - set(available))
    if missing:
        parser.error(f"cross-case raw sequence incomplete: missing {len(missing)}; first={missing[0]}")

    reference_cfg = dict(cfg["reference"])
    snapshots: dict[int, dict] = {}
    detections_by_frame: dict[int, list[dict]] = {}
    reference_rows: list[dict] = []
    detection_rows: list[dict] = []
    per_frame: list[dict] = []
    consistency_values: list[float] = []
    active: dict[int, dict] = {}
    next_id = 1
    physical: dict[int, tuple[dict, list[dict]]] = {}

    for frame_index, step in enumerate(required):
        assembled = assemble(str(case_dir), step, fmt="binary")
        absent = sorted({"vel1", "vel2", "omega3"} - set(assembled.variables))
        if absent:
            raise RuntimeError(f"step {step} lacks variables: {absent}")
        xi = np.flatnonzero(
            (assembled.x_cc >= cfg["analysis_xlim"][0])
            & (assembled.x_cc <= cfg["analysis_xlim"][1])
        )
        yi = np.flatnonzero(
            (assembled.y_cc >= cfg["analysis_ylim"][0])
            & (assembled.y_cc <= cfg["analysis_ylim"][1])
        )
        if not xi.size or not yi.size:
            raise RuntimeError("CC-FCV crop does not overlap the MFC grid")
        xi = np.arange(max(0, xi[0] - 3), min(assembled.x_cc.size, xi[-1] + 4))
        yi = np.arange(max(0, yi[0] - 3), min(assembled.y_cc.size, yi[-1] + 4))
        x = assembled.x_cc[xi].copy()
        y = assembled.y_cc[yi].copy()
        u = assembled.variables["vel1"][np.ix_(xi, yi)].copy()
        v = assembled.variables["vel2"][np.ix_(xi, yi)].copy()
        omega_mfc = assembled.variables["omega3"][np.ix_(xi, yi)].copy()
        fields = detector_tools.derive_fields(x, y, u, v)
        fluid = (
            reference_tools.geometry_fluid_mask(x, y)
            & (x[:, None] >= cfg["analysis_xlim"][0])
            & (x[:, None] <= cfg["analysis_xlim"][1])
            & (y[None, :] >= cfg["analysis_ylim"][0])
            & (y[None, :] <= cfg["analysis_ylim"][1])
        )
        finite = (
            np.isfinite(u[fluid]).all()
            and np.isfinite(v[fluid]).all()
            and np.isfinite(omega_mfc[fluid]).all()
        )
        if not finite:
            raise RuntimeError(f"non-finite cross-case field at step {step}")
        correlation = reference_tools.correlation(omega_mfc, fields["omega"], fluid)
        if correlation is not None:
            consistency_values.append(abs(float(correlation)))
        cores, thresholds = reference_tools.extract_cores(
            x, y, omega_mfc, fields["lci"], fluid, reference_cfg
        )
        associated, active, next_id = reference_tools.associate_cores(
            cores, frame_index, active, next_id, reference_cfg
        )
        for row in associated:
            row.update({
                "frame_index": frame_index,
                "source_step": step,
                "time": frame_index * cfg["snapshot_dt"],
            })
        reference_rows.extend(associated)
        snapshot = {
            "x": x, "y": y, "fluid": fluid, "reference": associated,
            "step": step, "omega": omega_mfc, **{k: fields[k] for k in ["q", "lci", "omega_abs"]},
        }
        detections = detector_tools.baseline_detect(snapshot, "q", frozen)
        detections_by_frame[frame_index] = detections
        for row in detections:
            detection_rows.append({
                "frame_index": frame_index, "source_step": step,
                "time": frame_index * cfg["snapshot_dt"], **row,
            })
        snapshots[frame_index] = {"reference": associated}
        per_frame.append({
            "frame_index": frame_index,
            "source_step": step,
            "reference_cores": len(associated),
            "cmcd_detections": len(detections),
            "vorticity_correlation": correlation,
            **thresholds,
        })
        if frame_index in cfg["comparison_frames"]:
            physical[frame_index] = (snapshot, detections)
        del assembled, u, v, omega_mfc

    evaluation = list(range(cfg["evaluation_frames"][0], cfg["evaluation_frames"][1] + 1))
    metrics, _ = detector_tools.evaluate(
        lambda frame: detections_by_frame.get(frame, []), snapshots, evaluation,
        cfg["reference_match_radius"], cfg["close_pair_maximum_separation"]
    )
    consistency_pass = (
        sum(value >= cfg["reference"]["minimum_vorticity_correlation"] for value in consistency_values)
        >= cfg["reference"]["minimum_consistency_frames"]
    )
    coverage_retention = metrics["coverage"] / max(float(source_metrics["coverage"]), 1.0e-300)
    close_retention = metrics["close_member_coverage"] / max(
        float(source_metrics["close_member_coverage"]), 1.0e-300
    )
    gates = {
        "raw_sequence_complete": "pass",
        "finite_fields": "pass",
        "derived_vorticity_consistency": "pass" if consistency_pass else "fail",
        "reference_catalogue": (
            "pass"
            if len(reference_rows) >= cfg["reference"]["minimum_reference_rows"]
            else "fail"
        ),
        "configuration_frozen_without_recalibration": "pass",
        "cross_case_coverage": "pass" if metrics["coverage"] >= cfg["minimum_coverage"] else "fail",
        "cross_case_close_core_coverage": (
            "pass" if metrics["close_member_coverage"] >= cfg["minimum_close_member_coverage"] else "fail"
        ),
        "source_metric_retention": (
            "pass"
            if min(coverage_retention, close_retention) >= cfg["minimum_source_metric_retention"]
            else "fail"
        ),
        "candidate_control": (
            "pass"
            if metrics["detection_to_reference_ratio"] <= cfg["maximum_detection_to_reference_ratio"]
            else "fail"
        ),
    }
    transfer_pass = all(value == "pass" for value in gates.values())
    write_csv(
        output / "ccfcv_reference_catalogue.csv", reference_rows,
        ["frame_index", "source_step", "time", "reference_id", "x_physical", "y_physical",
         "rotation_sign", "omega", "lambda_ci"]
    )
    write_csv(
        output / "ccfcv_cmcd_detections.csv", detection_rows,
        ["frame_index", "source_step", "time", "x", "y", "sign", "score", "method"]
    )
    write_csv(output / "ccfcv_per_frame.csv", per_frame, list(per_frame[0]))
    for frame_index, (snapshot, detections) in physical.items():
        draw_physical(output / f"ccfcv_physical_{frame_index:04d}.png", snapshot, detections)
    report = {
        "schema_version": 1,
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_name": cfg["method_name"],
        "case_id": cfg["case_id"],
        "alpha_deg": cfg["alpha_deg"],
        "frames": len(required),
        "evaluation_frames": evaluation,
        "frozen_cmcd_q_configuration": frozen,
        "source_case_metrics": source_metrics,
        "cross_case_metrics": metrics,
        "metric_retention": {
            "coverage": coverage_retention,
            "close_member_coverage": close_retention,
        },
        "vorticity_consistency": {
            "evaluated_frames": len(consistency_values),
            "passing_frames": sum(
                value >= cfg["reference"]["minimum_vorticity_correlation"]
                for value in consistency_values
            ),
            "minimum_absolute_correlation": min(consistency_values) if consistency_values else None,
            "median_absolute_correlation": statistics.median(consistency_values) if consistency_values else None,
        },
        "gates": gates,
        "claim_gate": (
            "frozen_cmcd_transfers_to_alpha30_expert_labels_and_third_case_next"
            if transfer_pass
            else "frozen_cmcd_cross_case_transfer_failed"
        ),
        "limitations": [
            "The alpha-30 case is independent of calibration but uses the same solver, geometry, Mach number, Reynolds number, and grid family.",
            "The criteria-derived catalogue is non-exhaustive and is not independent expert ground truth.",
            "Publication precision and recall still require blinded expert labels and a third flow topology.",
            "This is two-dimensional core localization, not three-dimensional vortex-tube segmentation."
        ],
    }
    (output / "ccfcv_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("CCFCV_STATUS=completed")
    print(f"CCFCV_FRAMES={len(required)}")
    print(f"CCFCV_COVERAGE={metrics['coverage']:.8f}")
    print(f"CCFCV_CLOSE_CORE_COVERAGE={metrics['close_member_coverage']:.8f}")
    print(f"CCFCV_DETECTION_REFERENCE_RATIO={metrics['detection_to_reference_ratio']:.8f}")
    print(f"CCFCV_CLAIM_GATE={report['claim_gate']}")
    print(f"CCFCV_REPORT={output / 'ccfcv_report.json'}")
    return 0 if transfer_pass else 8


if __name__ == "__main__":
    raise SystemExit(main())
