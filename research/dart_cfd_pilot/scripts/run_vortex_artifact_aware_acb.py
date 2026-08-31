#!/usr/bin/env python3
"""Artifact-aware ACB-CMCD using frozen candidate budgeting and physical vetoes.

The detector never reads visual labels while generating detections. The audit
did inform the choice of artifact families, however, so its metrics are an
honest development diagnostic rather than independent validation.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from collections import Counter, defaultdict
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


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def nearest_index(values: np.ndarray, target: float) -> int:
    index = int(np.searchsorted(values, target))
    if index <= 0:
        return 0
    if index >= len(values):
        return len(values) - 1
    return index if abs(values[index] - target) < abs(values[index - 1] - target) else index - 1


def derive_artifact_fields(
    x: np.ndarray,
    y: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    geometry_fluid: np.ndarray,
    gaussian_sigmas: list[float],
) -> dict:
    """Return kinematic fields used by the predeclared artifact vetoes."""
    from scipy.ndimage import distance_transform_edt, gaussian_filter

    dux, duy = np.gradient(u, x, y, edge_order=2)
    dvx, dvy = np.gradient(v, x, y, edge_order=2)
    omega = dvx - duy
    divergence = dux + dvy
    determinant = dux * dvy - duy * dvx
    discriminant = divergence * divergence - 4.0 * determinant
    lci = 0.5 * np.sqrt(np.maximum(-discriminant, 0.0))
    sxy = 0.5 * (duy + dvx)
    strain2 = dux * dux + dvy * dvy + 2.0 * sxy * sxy
    rotation2 = 0.5 * omega * omega
    q = np.maximum(0.5 * (rotation2 - strain2), 0.0)

    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    grid_scale = math.sqrt(abs(dx * dy))
    wall_distance = distance_transform_edt(geometry_fluid, sampling=(abs(dx), abs(dy)))
    q_scales = [gaussian_filter(q, sigma=float(sigma), mode="nearest") for sigma in gaussian_sigmas]

    smooth = gaussian_filter(q, sigma=1.0, mode="nearest")
    qx, qy = np.gradient(smooth, x, y, edge_order=2)
    qxx, qxy = np.gradient(qx, x, y, edge_order=2)
    _, qyy = np.gradient(qy, x, y, edge_order=2)
    trace = qxx + qyy
    root = np.sqrt(np.maximum((qxx - qyy) ** 2 + 4.0 * qxy * qxy, 0.0))
    eigen_max = 0.5 * (trace + root)
    eigen_min = 0.5 * (trace - root)
    maximum_curvature = np.maximum(np.abs(eigen_max), np.abs(eigen_min))
    minimum_curvature = np.minimum(np.abs(eigen_max), np.abs(eigen_min))
    hessian_compactness = np.where(
        (eigen_max < 0.0) & (eigen_min < 0.0),
        minimum_curvature / np.maximum(maximum_curvature, 1.0e-300),
        0.0,
    )
    return {
        "u": u,
        "v": v,
        "q": q,
        "omega": omega,
        "lci": lci,
        "divergence": divergence,
        "wall_distance": wall_distance,
        "q_scales": q_scales,
        "hessian_compactness": hessian_compactness,
        "grid_scale": grid_scale,
    }


def candidate_features(snapshot: dict, candidate: dict, cfg: dict) -> dict:
    """Measure wall, shock, topology, compactness, and multiscale evidence."""
    x, y = snapshot["x"], snapshot["y"]
    i = nearest_index(x, float(candidate["x"]))
    j = nearest_index(y, float(candidate["y"]))
    omega = snapshot["omega"]
    sign = int(candidate["sign"])
    eps = 1.0e-300
    radius_cells = int(cfg["local_patch_radius_cells"])
    i0, i1 = max(0, i - radius_cells), min(len(x), i + radius_cells + 1)
    j0, j1 = max(0, j - radius_cells), min(len(y), j + radius_cells + 1)
    patch = omega[i0:i1, j0:j1]
    weights = np.abs(patch)
    sign_coherence = float(
        np.sum(weights[(sign * patch) > 0.0]) / max(float(np.sum(weights)), eps)
    )

    compression = max(-float(snapshot["divergence"][i, j]), 0.0)
    omega_magnitude = abs(float(omega[i, j]))
    compression_fraction = compression / max(compression + omega_magnitude, eps)
    rotation_purity = min(2.0 * float(snapshot["lci"][i, j]) / max(omega_magnitude, eps), 1.0)

    ring_radius = float(cfg["ring_radius_cells"]) * float(snapshot["grid_scale"])
    u0 = float(snapshot["u"][i, j])
    v0 = float(snapshot["v"][i, j])
    tangential: list[float] = []
    radial: list[float] = []
    visited: set[tuple[int, int]] = set()
    sample_count = int(cfg["ring_samples"])
    for theta in np.linspace(0.0, 2.0 * math.pi, sample_count, endpoint=False):
        xp = float(candidate["x"]) + ring_radius * math.cos(theta)
        yp = float(candidate["y"]) + ring_radius * math.sin(theta)
        if xp < x[0] or xp > x[-1] or yp < y[0] or yp > y[-1]:
            continue
        ii, jj = nearest_index(x, xp), nearest_index(y, yp)
        if (ii, jj) in visited or not bool(snapshot["fluid"][ii, jj]):
            continue
        visited.add((ii, jj))
        du = float(snapshot["u"][ii, jj]) - u0
        dv = float(snapshot["v"][ii, jj]) - v0
        tangential.append(-du * math.sin(theta) + dv * math.cos(theta))
        radial.append(du * math.cos(theta) + dv * math.sin(theta))
    if tangential:
        tangential_array = np.asarray(tangential)
        radial_array = np.asarray(radial)
        ring_coherence = float(np.mean(sign * tangential_array > 0.0))
        radial_to_tangential = float(
            np.median(np.abs(radial_array)) / max(float(np.median(np.abs(tangential_array))), eps)
        )
    else:
        ring_coherence = 0.0
        radial_to_tangential = float("inf")
    ring_valid_fraction = len(tangential) / max(sample_count, 1)

    persistence_votes = 0
    for scaled_q in snapshot["q_scales"]:
        local = scaled_q[i0:i1, j0:j1]
        local_maximum = float(np.max(local)) if local.size else 0.0
        if float(scaled_q[i, j]) >= float(cfg["scale_peak_fraction"]) * max(local_maximum, eps):
            persistence_votes += 1
    scale_persistence = persistence_votes / max(len(snapshot["q_scales"]), 1)

    return {
        "grid_i": i,
        "grid_j": j,
        "wall_distance_cells": float(snapshot["wall_distance"][i, j]) / max(float(snapshot["grid_scale"]), eps),
        "compression_fraction": compression_fraction,
        "rotation_purity": rotation_purity,
        "sign_coherence": sign_coherence,
        "ring_coherence": ring_coherence,
        "ring_valid_fraction": ring_valid_fraction,
        "radial_to_tangential": radial_to_tangential,
        "scale_persistence": scale_persistence,
        "hessian_compactness": float(snapshot["hessian_compactness"][i, j]),
    }


def artifact_decision(features: dict, cfg: dict) -> tuple[bool, str, int, int]:
    """Apply predeclared physical vetoes; labels are deliberately absent."""
    if features["wall_distance_cells"] < float(cfg["minimum_wall_distance_cells"]):
        return False, "wall_mask_proximity", 0, 0
    if features["compression_fraction"] > float(cfg["maximum_compression_fraction"]):
        return False, "compressive_shock_signature", 0, 0

    tests = [
        features["rotation_purity"] >= float(cfg["minimum_rotation_purity"]),
        features["sign_coherence"] >= float(cfg["minimum_sign_coherence"]),
        features["scale_persistence"] >= float(cfg["minimum_scale_persistence"]),
        features["hessian_compactness"] >= float(cfg["minimum_hessian_compactness"]),
    ]
    if features["ring_valid_fraction"] >= float(cfg["minimum_ring_valid_fraction"]):
        tests.append(
            features["ring_coherence"] >= float(cfg["minimum_ring_coherence"])
            and features["radial_to_tangential"] <= float(cfg["maximum_radial_to_tangential"])
        )
    support = sum(bool(value) for value in tests)
    required = max(1, int(math.ceil(float(cfg["minimum_topology_support_fraction"]) * len(tests))))
    if support < required:
        return False, "insufficient_closed_core_topology", support, required
    return True, "accepted", support, required


def filter_candidates(candidates: list[dict], snapshot: dict, cfg: dict) -> tuple[list[dict], list[dict]]:
    accepted: list[dict] = []
    audit: list[dict] = []
    for rank, candidate in enumerate(candidates, start=1):
        features = candidate_features(snapshot, candidate, cfg)
        keep, reason, support, required = artifact_decision(features, cfg)
        row = {
            **candidate,
            **features,
            "uncapped_rank": rank,
            "artifact_accepted": keep,
            "artifact_reason": reason,
            "topology_support": support,
            "topology_required": required,
        }
        audit.append(row)
        if keep:
            accepted.append(row)
    return accepted, audit


def load_detections(path: Path) -> dict[int, list[dict]]:
    by_frame: dict[int, list[dict]] = defaultdict(list)
    for row in read_csv(path):
        by_frame[int(row["frame_index"])].append({
            "x": float(row["x"]),
            "y": float(row["y"]),
            "sign": int(row["rotation_sign"]),
            "score": float(row["q_score"]),
        })
    return by_frame


def point_detected(point: dict, detections: list[dict], radius: float) -> bool:
    return any(
        int(point["rotation_sign"]) == int(row["sign"])
        and math.hypot(
            float(point["x_physical"]) - float(row["x"]),
            float(point["y_physical"]) - float(row["y"]),
        ) <= radius
        for row in detections
    )


def confusion(rows: list[dict], prediction_key: str) -> dict:
    certain = [row for row in rows if row["is_vortex"] in {"yes", "no"}]
    tp = sum(row["is_vortex"] == "yes" and row[prediction_key] for row in certain)
    fp = sum(row["is_vortex"] == "no" and row[prediction_key] for row in certain)
    fn = sum(row["is_vortex"] == "yes" and not row[prediction_key] for row in certain)
    tn = sum(row["is_vortex"] == "no" and not row[prediction_key] for row in certain)
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    specificity = tn / max(tn + fp, 1)
    f1 = 2.0 * precision * recall / max(precision + recall, 1.0e-300)
    return {
        "evaluated": len(certain),
        "uncertain_excluded": len(rows) - len(certain),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "balanced_accuracy": 0.5 * (recall + specificity),
        "f1": f1,
    }


def score_blind_audit(
    key_path: Path,
    labels_path: Path,
    baseline: dict[int, list[dict]],
    artifact_aware: dict[int, list[dict]],
    radius: float,
) -> tuple[list[dict], dict]:
    labels = {row["audit_id"]: row for row in read_csv(labels_path)}
    rows: list[dict] = []
    for point in read_csv(key_path):
        label = labels.get(point["audit_id"])
        if label is None:
            raise RuntimeError(f"missing visual-audit label for {point['audit_id']}")
        frame = int(point["frame_index"])
        rows.append({
            **point,
            **label,
            "baseline_detected": point_detected(point, baseline.get(frame, []), radius),
            "artifact_aware_detected": point_detected(point, artifact_aware.get(frame, []), radius),
        })
    baseline_metrics = confusion(rows, "baseline_detected")
    artifact_metrics = confusion(rows, "artifact_aware_detected")
    morphology = {}
    for name in sorted({row["morphology"] for row in rows}):
        group = [row for row in rows if row["morphology"] == name and row["is_vortex"] in {"yes", "no"}]
        morphology[name] = {
            "count": len(group),
            "baseline_positive": sum(bool(row["baseline_detected"]) for row in group),
            "artifact_aware_positive": sum(bool(row["artifact_aware_detected"]) for row in group),
        }
    return rows, {
        "baseline_acb_cmcd": baseline_metrics,
        "artifact_aware_acb_cmcd": artifact_metrics,
        "precision_gain": artifact_metrics["precision"] - baseline_metrics["precision"],
        "recall_change": artifact_metrics["recall"] - baseline_metrics["recall"],
        "false_positive_reduction": baseline_metrics["false_positive"] - artifact_metrics["false_positive"],
        "morphology": morphology,
    }


def draw_physical(path: Path, snapshot: dict, baseline: list[dict], accepted: list[dict], rejected: list[dict]) -> None:
    import matplotlib.pyplot as plt

    field = np.where(snapshot["fluid"], snapshot["omega"], np.nan)
    limit = max(float(np.nanpercentile(np.abs(field), 99.5)), 1.0e-8)
    levels = np.linspace(-limit, limit, 81)
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2), sharex=True, sharey=True, constrained_layout=True)
    titles = ["(a) Frozen ACB-CMCD", "(b) Artifact-aware ACB-CMCD", "(c) Physical veto audit"]
    for axis, title in zip(axes, titles):
        axis.contourf(snapshot["x"], snapshot["y"], field.T, levels=levels, cmap="RdBu_r", extend="both")
        axis.set_title(title)
        axis.set_aspect("equal")
        axis.set_xlabel("x/c")
    axes[0].set_ylabel("y/c")
    if baseline:
        axes[0].scatter([d["x"] for d in baseline], [d["y"] for d in baseline], s=48, facecolors="none", edgecolors="#00bde3", linewidths=1.3)
    if accepted:
        for axis in axes[1:]:
            axis.scatter([d["x"] for d in accepted], [d["y"] for d in accepted], s=48, facecolors="none", edgecolors="#ffe000", linewidths=1.3)
    if rejected:
        axes[2].scatter([d["x"] for d in rejected], [d["y"] for d in rejected], marker="x", s=27, c="black", linewidths=0.8)
    fig.savefig(path, dpi=240, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--mfc-root", type=Path, required=True)
    parser.add_argument("--ccfcv-dir", type=Path, required=True)
    parser.add_argument("--acb-dir", type=Path, required=True)
    parser.add_argument("--expert-labels", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    cfg = json.loads((args.config or ROOT / "vortex_artifact_aware_acb.json").read_text())
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    acb_dir = args.acb_dir.resolve()
    locked = json.loads((acb_dir / "acb_cmcd_locked_configuration.json").read_text())
    if not locked.get("must_not_be_recalibrated_on_third_case"):
        parser.error("ACB input is not a locked configuration")

    base = load_sibling("artifact_base_acb", "run_vortex_acb_cmcd.py")
    reference_tools = load_sibling("artifact_reference_tools", "run_dart_stage5_raw_reference.py")
    detector_tools = load_sibling("artifact_detector_tools", "run_vortex_stage14_baselines.py")
    frozen = dict(locked["physics_configuration"])
    selector = dict(locked["candidate_budget_configuration"])

    sys.path.insert(0, str(args.mfc_root.resolve() / "toolchain"))
    from mfc.viz.reader import assemble, discover_timesteps

    required_steps = list(range(int(cfg["step_start"]), int(cfg["step_stop"]) + 1, int(cfg["step_stride"])))
    available = discover_timesteps(str(args.case_dir.resolve()), "binary")
    missing = sorted(set(required_steps) - set(available))
    if missing:
        parser.error(f"raw MFC sequence incomplete: missing {len(missing)}; first={missing[0]}")

    references_by_step: dict[int, list[dict]] = defaultdict(list)
    for row in read_csv(args.ccfcv_dir.resolve() / "ccfcv_reference_catalogue.csv"):
        references_by_step[int(row["source_step"])].append(row)
    baseline = load_detections(acb_dir / "acb_cmcd_detections.csv")

    artifact_by_frame: dict[int, list[dict]] = {}
    snapshots: dict[int, dict] = {}
    feature_rows: list[dict] = []
    detection_rows: list[dict] = []
    per_frame_rows: list[dict] = []
    rejection_counter: Counter[str] = Counter()
    for frame, step in enumerate(required_steps):
        assembled = assemble(str(args.case_dir.resolve()), step, fmt="binary")
        absent = sorted({"vel1", "vel2"} - set(assembled.variables))
        if absent:
            raise RuntimeError(f"step {step} lacks variables: {absent}")
        xi = np.flatnonzero((assembled.x_cc >= cfg["analysis_xlim"][0]) & (assembled.x_cc <= cfg["analysis_xlim"][1]))
        yi = np.flatnonzero((assembled.y_cc >= cfg["analysis_ylim"][0]) & (assembled.y_cc <= cfg["analysis_ylim"][1]))
        xi = np.arange(max(0, xi[0] - 3), min(assembled.x_cc.size, xi[-1] + 4))
        yi = np.arange(max(0, yi[0] - 3), min(assembled.y_cc.size, yi[-1] + 4))
        x, y = assembled.x_cc[xi].copy(), assembled.y_cc[yi].copy()
        u = assembled.variables["vel1"][np.ix_(xi, yi)].copy()
        v = assembled.variables["vel2"][np.ix_(xi, yi)].copy()
        geometry_fluid = reference_tools.geometry_fluid_mask(x, y)
        fluid = (
            geometry_fluid
            & (x[:, None] >= cfg["analysis_xlim"][0])
            & (x[:, None] <= cfg["analysis_xlim"][1])
            & (y[None, :] >= cfg["analysis_ylim"][0])
            & (y[None, :] <= cfg["analysis_ylim"][1])
        )
        fields = derive_artifact_fields(x, y, u, v, geometry_fluid, cfg["gaussian_sigmas"])
        snapshot = {
            "x": x,
            "y": y,
            "fluid": fluid,
            "reference": references_by_step.get(step, []),
            "step": step,
            **fields,
        }
        raw_candidates, threshold = base.all_q_candidates(snapshot, frozen)
        physically_valid, audit = filter_candidates(raw_candidates, snapshot, cfg)
        selected, selection = base.select_adaptive(physically_valid, selector)
        artifact_by_frame[frame] = selected
        snapshots[frame] = snapshot
        for row in audit:
            rejection_counter[row["artifact_reason"]] += int(not row["artifact_accepted"])
            feature_rows.append({"frame_index": frame, "source_step": step, **row})
        for rank, row in enumerate(selected, start=1):
            detection_rows.append({
                "frame_index": frame,
                "source_step": step,
                "rank": rank,
                "x": row["x"],
                "y": row["y"],
                "rotation_sign": row["sign"],
                "q_score": row["score"],
                "artifact_reason": row["artifact_reason"],
            })
        per_frame_rows.append({
            "frame_index": frame,
            "source_step": step,
            "baseline_detections": len(baseline.get(frame, [])),
            "uncapped_q_candidates": len(raw_candidates),
            "physically_valid_candidates": len(physically_valid),
            "artifact_aware_detections": len(selected),
            "robust_q_threshold": threshold["robust_q_threshold"],
            **selection,
        })
        del assembled, u, v

    holdout = list(range(int(cfg["holdout_frames"][0]), int(cfg["holdout_frames"][1]) + 1))
    reference_metrics, _ = detector_tools.evaluate(
        lambda frame: artifact_by_frame.get(frame, []),
        snapshots,
        holdout,
        float(cfg["reference_match_radius"]),
        float(cfg["close_pair_maximum_separation"]),
    )
    audit_rows, expert = score_blind_audit(
        acb_dir / "acb_cmcd_blind_key.csv",
        args.expert_labels.resolve(),
        baseline,
        artifact_by_frame,
        float(cfg["expert_match_radius"]),
    )

    baseline_expert = expert["baseline_acb_cmcd"]
    artifact_expert = expert["artifact_aware_acb_cmcd"]
    shock_wall = expert["morphology"].get("shock_or_wall", {})
    shock_wall_reduction = int(shock_wall.get("baseline_positive", 0)) - int(shock_wall.get("artifact_aware_positive", 0))
    gates = {
        "technical_execution": "pass",
        "labels_posthoc_only": "pass",
        "expert_audit_complete": "pass" if artifact_expert["evaluated"] >= int(cfg["minimum_certain_audit_labels"]) else "fail",
        "expert_precision_not_worse": "pass" if artifact_expert["precision"] >= baseline_expert["precision"] else "fail",
        "expert_recall_preserved": "pass" if expert["recall_change"] >= -float(cfg["maximum_expert_recall_loss"]) else "fail",
        "shock_wall_false_positive_reduction": "pass" if shock_wall_reduction >= int(cfg["minimum_shock_wall_reduction"]) else "fail",
    }
    scientific_pass = all(value == "pass" for value in gates.values())

    for frame in cfg["physical_figure_frames"]:
        rejected = [row for row in feature_rows if int(row["frame_index"]) == int(frame) and not row["artifact_accepted"]]
        draw_physical(
            output / f"artifact_aware_acb_physical_{int(frame):04d}.png",
            snapshots[int(frame)],
            baseline.get(int(frame), []),
            artifact_by_frame.get(int(frame), []),
            rejected,
        )

    write_csv(output / "artifact_aware_acb_candidate_features.csv", feature_rows, list(feature_rows[0]))
    write_csv(output / "artifact_aware_acb_detections.csv", detection_rows, list(detection_rows[0]))
    write_csv(output / "artifact_aware_acb_per_frame.csv", per_frame_rows, list(per_frame_rows[0]))
    write_csv(output / "artifact_aware_acb_blind_audit.csv", audit_rows, list(audit_rows[0]))
    (output / "artifact_aware_acb_locked_configuration.json").write_text(json.dumps({
        "schema_version": 1,
        "method_name": cfg["method_name"],
        "base_physics_configuration": frozen,
        "candidate_budget_configuration": selector,
        "artifact_veto_configuration": {key: cfg[key] for key in [
            "minimum_wall_distance_cells", "maximum_compression_fraction", "minimum_rotation_purity",
            "minimum_sign_coherence", "minimum_ring_coherence", "maximum_radial_to_tangential",
            "minimum_scale_persistence", "minimum_hessian_compactness", "minimum_topology_support_fraction",
        ]},
        "configuration_status": "locked_for_new_case" if scientific_pass else "diagnostic_requires_revision",
        "must_not_be_recalibrated_on_new_case": True,
    }, indent=2) + "\n")

    report = {
        "schema_version": 1,
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_name": cfg["method_name"],
        "case_id": cfg["case_id"],
        "protocol": {
            "detections_generated_before_labels_loaded": True,
            "visual_audit_informed_feature_design": True,
            "expert_labels_used_for_numeric_threshold_optimization": False,
            "physics_reference_used_for_detection": False,
            "uncertain_labels_excluded_from_confusion_matrix": True,
            "visual_audit_role": "development resubstitution diagnostic; not independent validation",
        },
        "expert_audit": expert,
        "physics_reference_secondary_metrics": reference_metrics,
        "artifact_rejections": dict(rejection_counter),
        "gates": gates,
        "claim_gate": "artifact_aware_acb_ready_for_new_case_blind_validation" if scientific_pass else "artifact_aware_acb_requires_physics_gate_revision",
        "limitations": [
            "The 36-image visual audit is stratified and cannot estimate prevalence-weighted precision.",
            "Repeated shock and wall archetypes are not statistically independent samples.",
            "The audit informed artifact-family design, so its metrics are not a blind generalization result.",
            "Claude labels are a development diagnostic, not final publication ground truth.",
            "The next publication test requires a new flow case, frozen parameters, and independent human annotation.",
            "The method detects two-dimensional vortex cores, not three-dimensional vortex tubes.",
        ],
    }
    (output / "artifact_aware_acb_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("ARTIFACT_AWARE_ACB_STATUS=completed")
    print(f"ARTIFACT_AWARE_ACB_BASELINE_EXPERT_PRECISION={baseline_expert['precision']:.8f}")
    print(f"ARTIFACT_AWARE_ACB_EXPERT_PRECISION={artifact_expert['precision']:.8f}")
    print(f"ARTIFACT_AWARE_ACB_BASELINE_EXPERT_RECALL={baseline_expert['recall']:.8f}")
    print(f"ARTIFACT_AWARE_ACB_EXPERT_RECALL={artifact_expert['recall']:.8f}")
    print(f"ARTIFACT_AWARE_ACB_SHOCK_WALL_FP_REDUCTION={shock_wall_reduction}")
    print(f"ARTIFACT_AWARE_ACB_CLAIM_GATE={report['claim_gate']}")
    print(f"ARTIFACT_AWARE_ACB_REPORT={output / 'artifact_aware_acb_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
