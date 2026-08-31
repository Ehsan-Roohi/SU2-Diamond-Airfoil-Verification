#!/usr/bin/env python3
"""Benchmark variable-kernel Gamma detection and a Galilean-invariant adaptation."""
from __future__ import annotations

import argparse
import csv
import importlib.util
import itertools
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def load_sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


stage8 = load_sibling("stage8_stage15", "run_dart_stage8_physics_catalogue.py")
stage14 = None


def write_csv(path, rows, fields=None):
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w", newline="") as stream:
        if not fields:
            return
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def shifted_slices(shape, di, dj):
    src_i = slice(max(0, -di), min(shape[0], shape[0] - di))
    dst_i = slice(max(0, di), min(shape[0], shape[0] + di))
    src_j = slice(max(0, -dj), min(shape[1], shape[1] - dj))
    dst_j = slice(max(0, dj), min(shape[1], shape[1] + dj))
    return src_i, src_j, dst_i, dst_j


def gamma_pair(x, y, u, v, fluid, radius):
    """Return standard Gamma1 and locally convected Gamma2 on a circular stencil."""
    shape = u.shape
    offsets = [
        (di, dj)
        for di in range(-radius, radius + 1)
        for dj in range(-radius, radius + 1)
        if di * di + dj * dj <= radius * radius
    ]
    ubar = np.zeros(shape, float)
    vbar = np.zeros(shape, float)
    nbar = np.zeros(shape, float)
    for di, dj in offsets:
        si, sj, ti, tj = shifted_slices(shape, di, dj)
        valid = fluid[si, sj]
        ubar[ti, tj] += np.where(valid, u[si, sj], 0.0)
        vbar[ti, tj] += np.where(valid, v[si, sj], 0.0)
        nbar[ti, tj] += valid
    ubar /= np.maximum(nbar, 1.0)
    vbar /= np.maximum(nbar, 1.0)
    gamma1 = np.zeros(shape, float)
    gamma2 = np.zeros(shape, float)
    count = np.zeros(shape, float)
    eps = 1.0e-14
    for di, dj in offsets:
        if di == 0 and dj == 0:
            continue
        si, sj, ti, tj = shifted_slices(shape, di, dj)
        valid = fluid[si, sj] & fluid[ti, tj]
        rx = float(np.median(x[si] - x[ti]))
        ry = float(np.median(y[sj] - y[tj]))
        distance = math.hypot(rx, ry)
        um = u[si, sj]
        vm = v[si, sj]
        den1 = distance * np.sqrt(um * um + vm * vm) + eps
        du = um - ubar[ti, tj]
        dv = vm - vbar[ti, tj]
        den2 = distance * np.sqrt(du * du + dv * dv) + eps
        gamma1[ti, tj] += np.where(valid, (rx * vm - ry * um) / den1, 0.0)
        gamma2[ti, tj] += np.where(valid, (rx * dv - ry * du) / den2, 0.0)
        count[ti, tj] += valid
    gamma1 /= np.maximum(count, 1.0)
    gamma2 /= np.maximum(count, 1.0)
    gamma1[~fluid] = np.nan
    gamma2[~fluid] = np.nan
    return gamma1, gamma2


def variable_gamma(x, y, u, v, fluid, kernel_sizes, threshold):
    radii = [(int(size) - 1) // 2 for size in kernel_sizes]
    standard = []
    invariant = []
    for radius in radii:
        gamma1, gamma2 = gamma_pair(x, y, u, v, fluid, radius)
        standard.append(gamma1)
        invariant.append(gamma2)

    def combine(fields):
        stack = np.stack(fields)
        safe = np.where(np.isfinite(stack), np.abs(stack), -np.inf)
        scale = np.argmax(safe, axis=0)
        combined = np.take_along_axis(stack, scale[None, ...], axis=0)[0]
        sign = np.sign(combined)
        support = np.sum((np.abs(stack) >= threshold) & (np.sign(stack) == sign[None, ...]), axis=0)
        return combined, support, scale

    g1, g1_support, g1_scale = combine(standard)
    g2, g2_support, g2_scale = combine(invariant)
    return {
        "gamma1": g1,
        "gamma1_support": g1_support,
        "gamma1_scale": g1_scale,
        "gamma2": g2,
        "gamma2_support": g2_support,
        "gamma2_scale": g2_scale,
        "radii": radii,
    }


def raw_candidates(x, y, gamma, support, scale, radii, fluid, threshold, minimum_support, rotating=None, method="gamma"):
    score = np.abs(gamma)
    finite_score = np.where(np.isfinite(score), score, -np.inf)
    padded = np.pad(finite_score, 1, mode="edge")
    local_maximum = np.maximum.reduce(
        [padded[di:di + score.shape[0], dj:dj + score.shape[1]] for di in range(3) for dj in range(3)]
    )
    peaks = score == local_maximum
    mask = peaks & fluid & np.isfinite(score) & (score >= threshold) & (support >= minimum_support)
    if rotating is not None:
        mask &= rotating
    rows = []
    for i, j in np.argwhere(mask):
        if i < 2 or j < 2 or i >= len(x) - 2 or j >= len(y) - 2:
            continue
        rows.append({
            "x": float(x[i]),
            "y": float(y[j]),
            "sign": 1 if gamma[i, j] >= 0 else -1,
            "score": float(score[i, j]),
            "gamma": float(gamma[i, j]),
            "scale_support": int(support[i, j]),
            "radius_cells": int(radii[int(scale[i, j])]),
            "method": method,
        })
    return sorted(rows, key=lambda row: -row["score"])


def nms(candidates, radius, maximum):
    accepted = []
    for candidate in candidates:
        if any(
            candidate["sign"] == prior["sign"]
            and math.hypot(candidate["x"] - prior["x"], candidate["y"] - prior["y"]) < radius
            for prior in accepted
        ):
            continue
        accepted.append(candidate)
        if len(accepted) >= maximum:
            break
    return accepted


def evaluate_candidates(raw, snapshots, frames, parameters, cfg):
    return stage14.evaluate(
        lambda frame: nms(raw.get(frame, []), float(parameters["nms_radius"]), int(parameters["maximum_detections"])),
        snapshots,
        frames,
        cfg["reference_match_radius"],
        cfg["close_pair_maximum_separation"],
    )


def calibrate(raw, snapshots, frames, cfg, method):
    rows = []
    for index, (radius, maximum) in enumerate(itertools.product(cfg["nms_radius_grid"], cfg["maximum_detections_grid"])):
        parameters = {"nms_radius": radius, "maximum_detections": maximum}
        metrics, _ = evaluate_candidates(raw, snapshots, frames, parameters, cfg)
        objective = (
            metrics["coverage"]
            + cfg["close_pair_objective_weight"] * metrics["close_member_coverage"]
            - cfg["candidate_penalty"] * max(metrics["detection_to_reference_ratio"] - cfg["target_maximum_detection_to_reference_ratio"], 0.0)
        )
        rows.append({"method": method, "configuration_id": index, **parameters, **metrics, "objective": objective})
    feasible = [row for row in rows if row["detection_to_reference_ratio"] <= cfg["target_maximum_detection_to_reference_ratio"]]
    pool = feasible or rows
    selected = max(pool, key=lambda row: (row["objective"], row["coverage"], row["close_member_coverage"], -row["detection_to_reference_ratio"]))
    return {"nms_radius": selected["nms_radius"], "maximum_detections": selected["maximum_detections"]}, rows


def draw_comparison(path, snapshot, methods, title):
    import matplotlib.pyplot as plt

    masked = np.where(snapshot["fluid"], snapshot["omega"], np.nan)
    limit = max(float(np.nanpercentile(np.abs(masked), 99.5)), 1.0e-8)
    panels = [
        ("stage13", "CMCD"),
        ("q", "Q criterion"),
        ("vgcm_gamma1", r"Optimized ASDA $\Gamma_1$"),
        ("gi_vgcm", r"GI-VGCM $\Gamma_2$ + rotation veto"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(13, 10), sharex=True, sharey=True, constrained_layout=True)
    for axis, (key, label) in zip(axes.flat, panels):
        axis.contourf(snapshot["x"], snapshot["y"], masked.T, levels=np.linspace(-limit, limit, 81), cmap="RdBu_r", extend="both")
        detections = methods.get(key, [])
        if detections:
            axis.scatter([row["x"] for row in detections], [row["y"] for row in detections], s=48, facecolors="none", edgecolors="#ffe000", linewidths=1.4)
        reference = snapshot["reference"]
        axis.scatter([float(row["x_physical"]) for row in reference], [float(row["y_physical"]) for row in reference], marker="+", s=32, c="black", linewidths=1.1)
        axis.set_title(label)
        axis.set_aspect("equal")
        axis.set(xlabel="x", ylabel="y")
    fig.suptitle(title)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def synthetic_invariance_check():
    axis = np.linspace(-1.0, 1.0, 81)
    x, y = axis, axis
    xx, yy = np.meshgrid(x, y, indexing="ij")
    fluid = np.ones_like(xx, dtype=bool)
    u = -yy * np.exp(-(xx * xx + yy * yy) / 0.25)
    v = xx * np.exp(-(xx * xx + yy * yy) / 0.25)
    _, base = gamma_pair(x, y, u, v, fluid, 4)
    _, translated = gamma_pair(x, y, u + 12.0, v - 4.0, fluid, 4)
    difference = float(np.nanmax(np.abs(base - translated)))
    peak = np.unravel_index(np.nanargmax(np.abs(base)), base.shape)
    location_error = math.hypot(float(x[peak[0]]), float(y[peak[1]]))
    return {"translation_maximum_difference": difference, "core_location_error": location_error, "pass": difference <= 1.0e-10 and location_error <= 0.05}


def main():
    global stage14
    stage14 = load_sibling("stage14_stage15", "run_vortex_stage14_baselines.py")
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--mfc-root", type=Path, required=True)
    parser.add_argument("--stage8-catalogue", type=Path, required=True)
    parser.add_argument("--stage13-detections", type=Path, required=True)
    parser.add_argument("--stage14-report", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads((args.config or ROOT / "dart_stage15.json").read_text())
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.mfc_root.resolve() / "toolchain"))
    from mfc.viz.reader import assemble, discover_timesteps

    required = list(range(cfg["step_start"], cfg["step_stop"] + 1, cfg["step_stride"]))
    available = discover_timesteps(str(args.case_dir.resolve()), "binary")
    missing = sorted(set(required) - set(available))
    if missing:
        parser.error(f"raw MFC sequence incomplete: missing {len(missing)}; first={missing[0]}")
    references = {}
    with args.stage8_catalogue.open(newline="") as stream:
        for row in csv.DictReader(stream):
            references.setdefault(int(row["source_step"]), []).append(row)
    stage13 = stage14.load_stage13(args.stage13_detections)
    stage14_report = json.loads(args.stage14_report.read_text())
    q_parameters = stage14_report["selected_baseline_configurations"]["q"]
    snapshots = {}
    figures = {}
    raw = {"vgcm_gamma1": {}, "gi_vgcm": {}}
    q_detections = {}
    threshold = float(cfg["gamma1_minimum"])
    minimum_support = int(cfg["minimum_consistent_scales"])
    for frame, step in enumerate(required):
        assembled = assemble(str(args.case_dir.resolve()), step, fmt="binary")
        xi = np.flatnonzero((assembled.x_cc >= cfg["analysis_xlim"][0]) & (assembled.x_cc <= cfg["analysis_xlim"][1]))
        yi = np.flatnonzero((assembled.y_cc >= cfg["analysis_ylim"][0]) & (assembled.y_cc <= cfg["analysis_ylim"][1]))
        xi = np.arange(max(0, xi[0] - 3), min(assembled.x_cc.size, xi[-1] + 4))
        yi = np.arange(max(0, yi[0] - 3), min(assembled.y_cc.size, yi[-1] + 4))
        x = assembled.x_cc[xi].copy()
        y = assembled.y_cc[yi].copy()
        u = assembled.variables["vel1"][np.ix_(xi, yi)].copy()
        v = assembled.variables["vel2"][np.ix_(xi, yi)].copy()
        fields = stage14.derive_fields(x, y, u, v)
        fluid = stage8.geometry_fluid_mask(x, y) & (x[:, None] >= cfg["analysis_xlim"][0]) & (x[:, None] <= cfg["analysis_xlim"][1]) & (y[None, :] >= cfg["analysis_ylim"][0]) & (y[None, :] <= cfg["analysis_ylim"][1])
        gamma = variable_gamma(x, y, u, v, fluid, cfg["gamma_kernel_sizes"], threshold)
        raw["vgcm_gamma1"][frame] = raw_candidates(x, y, gamma["gamma1"], gamma["gamma1_support"], gamma["gamma1_scale"], gamma["radii"], fluid, threshold, minimum_support, method="vgcm_gamma1")
        rotating = (fields["q"] > 0.0) & (fields["lci"] > 0.0)
        raw["gi_vgcm"][frame] = raw_candidates(x, y, gamma["gamma2"], gamma["gamma2_support"], gamma["gamma2_scale"], gamma["radii"], fluid, threshold, minimum_support, rotating=rotating, method="gi_vgcm")
        snapshot = {"x": x, "y": y, "fluid": fluid, "reference": references.get(step, []), "step": step, **fields}
        snapshots[frame] = {"reference": snapshot["reference"]}
        q_detections[frame] = stage14.baseline_detect(snapshot, "q", q_parameters)
        if frame in cfg["comparison_frames"]:
            figures[frame] = snapshot
        del assembled, u, v, gamma

    calibration = list(range(cfg["calibration_frames"][0], cfg["calibration_frames"][1] + 1))
    holdout = list(range(cfg["holdout_frames"][0], cfg["holdout_frames"][1] + 1))
    selected = {}
    sweep = []
    holdout_metrics = {}
    selected_detections = {}
    for method in ["vgcm_gamma1", "gi_vgcm"]:
        parameters, rows = calibrate(raw[method], snapshots, calibration, cfg, method)
        selected[method] = parameters
        sweep.extend(rows)
        metrics, detections = evaluate_candidates(raw[method], snapshots, holdout, parameters, cfg)
        holdout_metrics[method] = metrics
        selected_detections[method] = {
            frame: nms(raw[method].get(frame, []), float(parameters["nms_radius"]), int(parameters["maximum_detections"]))
            for frame in range(len(required))
        }
    holdout_metrics["stage13"], _ = stage14.evaluate(lambda frame: stage13.get(frame, []), snapshots, holdout, cfg["reference_match_radius"], cfg["close_pair_maximum_separation"])
    holdout_metrics["q"], _ = stage14.evaluate(lambda frame: q_detections.get(frame, []), snapshots, holdout, cfg["reference_match_radius"], cfg["close_pair_maximum_separation"])
    detection_rows = []
    for method in ["vgcm_gamma1", "gi_vgcm"]:
        for frame, detections in selected_detections[method].items():
            for row in detections:
                detection_rows.append({"frame_index": frame, "source_step": required[frame], **row})
    write_csv(out / "stage15_detections.csv", detection_rows)
    write_csv(out / "stage15_calibration_sweep.csv", sweep)
    for frame, snapshot in figures.items():
        draw_comparison(
            out / f"stage15_comparison_{frame:04d}.png",
            snapshot,
            {
                "stage13": stage13.get(frame, []),
                "q": q_detections.get(frame, []),
                "vgcm_gamma1": selected_detections["vgcm_gamma1"].get(frame, []),
                "gi_vgcm": selected_detections["gi_vgcm"].get(frame, []),
            },
            f"Variable-Gamma physical comparison: frame {frame}, step {snapshot['step']}",
        )
    synthetic = synthetic_invariance_check()
    q_metrics = holdout_metrics["q"]
    gi_metrics = holdout_metrics["gi_vgcm"]
    gates = {
        "raw_sequence_complete": "pass",
        "synthetic_galilean_invariance": "pass" if synthetic["pass"] else "fail",
        "stage13_holdout_reproduced": "pass" if holdout_metrics["stage13"]["coverage"] >= 0.80 else "fail",
        "q_holdout_reproduced": "pass" if abs(q_metrics["coverage"] - stage14_report["holdout_metrics"]["q"]["coverage"]) <= 1.0e-12 else "fail",
        "gi_vgcm_candidate_control": "pass" if gi_metrics["detection_to_reference_ratio"] <= cfg["maximum_gi_vgcm_detection_to_reference_ratio"] else "fail",
        "gi_vgcm_beats_q_coverage": "pass" if gi_metrics["coverage"] > q_metrics["coverage"] else "fail",
        "gi_vgcm_beats_q_close_cores": "pass" if gi_metrics["close_member_coverage"] > q_metrics["close_member_coverage"] else "fail",
    }
    superior = all(gates[key] == "pass" for key in ["gi_vgcm_candidate_control", "gi_vgcm_beats_q_coverage", "gi_vgcm_beats_q_close_cores"])
    report = {
        "schema_version": 1,
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_definition": {
            "kernel_sizes": cfg["gamma_kernel_sizes"],
            "gamma_threshold": threshold,
            "minimum_consistent_scales": minimum_support,
            "gi_vgcm": "variable-kernel Gamma2 with local convection removal and Q/lambda_ci rotation veto",
        },
        "selected_configurations": selected,
        "holdout_metrics": holdout_metrics,
        "synthetic_invariance": synthetic,
        "gates": gates,
        "claim_gate": "gi_vgcm_candidate_for_independent_cross_case_validation" if superior else "gi_vgcm_benchmark_complete_not_superior_to_q",
        "limitations": [
            "Stage 8 remains a non-exhaustive, criteria-derived reference and can favor derivative-based methods.",
            "The temporal holdout belongs to one MFC case; cross-case testing is still required.",
            "The GI-VGCM adaptation is a two-dimensional core detector, not a three-dimensional vortex-tube segmenter.",
            "Independent expert labels from the Stage 14 blinded pack remain required for publication precision and recall.",
        ],
    }
    (out / "stage15_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("STAGE15_STATUS=completed")
    print(f"STAGE15_ASDA_GAMMA1_COVERAGE={holdout_metrics['vgcm_gamma1']['coverage']:.6f}")
    print(f"STAGE15_GI_VGCM_COVERAGE={gi_metrics['coverage']:.6f}")
    print(f"STAGE15_GI_VGCM_CLOSE_COVERAGE={gi_metrics['close_member_coverage']:.6f}")
    print(f"STAGE15_Q_COVERAGE={q_metrics['coverage']:.6f}")
    print(f"STAGE15_CLAIM_GATE={report['claim_gate']}")
    print(f"STAGE15_REPORT={out / 'stage15_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
