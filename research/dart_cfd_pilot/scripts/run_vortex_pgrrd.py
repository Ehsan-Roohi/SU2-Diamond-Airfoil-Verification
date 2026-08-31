#!/usr/bin/env python3
"""Physics-Guided Rotating-Region Deblending (PG-RRD)."""
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
stage8 = None
stage14 = None


def load_sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_csv(path, rows, fields=None):
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w", newline="") as stream:
        if not fields:
            return
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def positive_quantile_threshold(field, fluid, quantile):
    valid = fluid & np.isfinite(field) & (field > 0.0)
    values = field[valid]
    if values.size == 0:
        return math.inf
    return float(np.quantile(values, float(quantile)))


def local_peak_mask(field, fluid):
    from scipy.ndimage import maximum_filter

    safe = np.where(fluid & np.isfinite(field), field, -np.inf)
    return fluid & np.isfinite(field) & (safe == maximum_filter(safe, size=3, mode="nearest"))


def _refine_center(x, y, omega, q, lci, i, j, half_width):
    i0, i1 = max(0, i - half_width), min(len(x), i + half_width + 1)
    j0, j1 = max(0, j - half_width), min(len(y), j + half_width + 1)
    sign = 1 if omega[i, j] >= 0.0 else -1
    same_sign = np.sign(omega[i0:i1, j0:j1]) == sign
    weight = np.sqrt(np.maximum(q[i0:i1, j0:j1], 0.0) * np.maximum(lci[i0:i1, j0:j1], 0.0))
    weight = np.where(same_sign & np.isfinite(weight), weight, 0.0)
    total = float(np.sum(weight))
    if total <= 0.0:
        return float(x[i]), float(y[j])
    xx, yy = np.meshgrid(x[i0:i1], y[j0:j1], indexing="ij")
    return float(np.sum(weight * xx) / total), float(np.sum(weight * yy) / total)


def region_candidates(snapshot, seed_quantile, low_quantile, boundary_margin_cells, refinement_half_width):
    """Find independently seeded subcores inside Q/lambda_ci-positive rotating regions."""
    from scipy.ndimage import distance_transform_edt, maximum_filter

    q = snapshot["q"]
    lci = snapshot["lci"]
    omega_abs = snapshot["omega_abs"]
    omega = snapshot["omega"]
    fluid = snapshot["fluid"]
    interior = fluid & (distance_transform_edt(fluid) > float(boundary_margin_cells))

    q_seed = positive_quantile_threshold(q, interior, seed_quantile)
    lci_seed = positive_quantile_threshold(lci, interior, seed_quantile)
    omega_seed = positive_quantile_threshold(omega_abs, interior, seed_quantile)
    q_low = positive_quantile_threshold(q, interior, low_quantile)
    lci_low = positive_quantile_threshold(lci, interior, low_quantile)
    rotating_region = interior & (q >= q_low) & (lci >= lci_low)

    q_peaks = local_peak_mask(q, interior) & (q >= q_seed)
    lci_peaks = local_peak_mask(lci, interior) & (lci >= lci_seed)
    omega_peaks = local_peak_mask(omega_abs, interior) & (omega_abs >= omega_seed)
    nearby_q = maximum_filter(q_peaks.astype(np.uint8), size=3, mode="nearest")
    nearby_lci = maximum_filter(lci_peaks.astype(np.uint8), size=3, mode="nearest")
    nearby_omega = maximum_filter(omega_peaks.astype(np.uint8), size=3, mode="nearest")
    source_support = nearby_q + nearby_lci + nearby_omega
    seeds = rotating_region & (q_peaks | lci_peaks | omega_peaks)

    rows = []
    for i, j in np.argwhere(seeds):
        x0, y0 = _refine_center(
            snapshot["x"], snapshot["y"], omega, q, lci, int(i), int(j), int(refinement_half_width)
        )
        q_ratio = float(q[i, j] / max(q_seed, 1.0e-300))
        lci_ratio = float(lci[i, j] / max(lci_seed, 1.0e-300))
        omega_ratio = float(omega_abs[i, j] / max(omega_seed, 1.0e-300))
        sources = []
        if nearby_q[i, j]:
            sources.append("q")
        if nearby_lci[i, j]:
            sources.append("lci")
        if nearby_omega[i, j]:
            sources.append("omega")
        rows.append({
            "x": x0,
            "y": y0,
            "sign": 1 if omega[i, j] >= 0.0 else -1,
            "score": math.sqrt(max(q_ratio, 0.0) * max(lci_ratio, 0.0)) + 0.15 * max(omega_ratio, 0.0),
            "source_support": int(source_support[i, j]),
            "sources": ";".join(sources),
            "q_ratio": q_ratio,
            "lci_ratio": lci_ratio,
            "omega_ratio": omega_ratio,
            "method": "rotating_region_deblend",
        })

    merged = []
    grid_merge_radius = 1.5 * max(
        float(np.median(np.diff(snapshot["x"]))), float(np.median(np.diff(snapshot["y"])))
    )
    for row in sorted(rows, key=lambda item: -item["score"]):
        if any(
            row["sign"] == prior["sign"]
            and math.hypot(row["x"] - prior["x"], row["y"] - prior["y"]) <= grid_merge_radius
            for prior in merged
        ):
            continue
        merged.append(row)
    return merged


def select_candidates(candidates, minimum_source_support, nms_radius, maximum_detections):
    accepted = []
    for candidate in sorted(candidates, key=lambda row: -row["score"]):
        if int(candidate["source_support"]) < int(minimum_source_support):
            continue
        if any(
            candidate["sign"] == prior["sign"]
            and math.hypot(candidate["x"] - prior["x"], candidate["y"] - prior["y"]) < float(nms_radius)
            for prior in accepted
        ):
            continue
        accepted.append(candidate)
        if len(accepted) >= int(maximum_detections):
            break
    return accepted


def evaluate_configuration(raw, snapshots, frames, parameters, cfg):
    return stage14.evaluate(
        lambda frame: select_candidates(
            raw[float(parameters["seed_quantile"])].get(frame, []),
            parameters["minimum_source_support"],
            parameters["nms_radius"],
            parameters["maximum_detections"],
        ),
        snapshots,
        frames,
        cfg["reference_match_radius"],
        cfg["close_pair_maximum_separation"],
    )


def calibrate(raw, snapshots, calibration, cfg):
    keys = ["seed_quantile", "minimum_source_support", "nms_radius", "maximum_detections"]
    rows = []
    grid = [cfg["calibration_grid"][key] for key in keys]
    for index, values in enumerate(itertools.product(*grid)):
        parameters = dict(zip(keys, values))
        metrics, _ = evaluate_configuration(raw, snapshots, calibration, parameters, cfg)
        excess = max(metrics["detection_to_reference_ratio"] - cfg["target_maximum_detection_to_reference_ratio"], 0.0)
        objective = (
            metrics["coverage"]
            + cfg["close_pair_objective_weight"] * metrics["close_member_coverage"]
            - cfg["candidate_penalty"] * excess
        )
        rows.append({"configuration_id": index, **parameters, **metrics, "objective": objective})
    feasible = [
        row for row in rows
        if row["detection_to_reference_ratio"] <= cfg["target_maximum_detection_to_reference_ratio"]
    ]
    pool = feasible or rows
    selected = max(
        pool,
        key=lambda row: (
            row["objective"], row["close_member_coverage"], row["coverage"], -row["detection_to_reference_ratio"]
        ),
    )
    return {key: selected[key] for key in keys}, rows


def synthetic_velocity(axis, separation=0.12, core_radius=0.055, translation=(0.0, 0.0), shear=0.0):
    xx, yy = np.meshgrid(axis, axis, indexing="ij")
    u = np.zeros_like(xx)
    v = np.zeros_like(xx)
    for center_x in (-0.5 * separation, 0.5 * separation):
        dx = xx - center_x
        radius2 = dx * dx + yy * yy
        factor = (1.0 - np.exp(-radius2 / (core_radius * core_radius))) / (radius2 + 1.0e-14)
        u -= yy * factor
        v += dx * factor
    u += float(translation[0]) + float(shear) * yy
    v += float(translation[1])
    return u, v


def _match_centers(detections, centers, radius):
    remaining = set(range(len(centers)))
    matched = 0
    for detection in sorted(detections, key=lambda row: -row["score"]):
        if detection["sign"] != 1 or not remaining:
            continue
        target = min(remaining, key=lambda k: math.hypot(detection["x"] - centers[k][0], detection["y"] - centers[k][1]))
        if math.hypot(detection["x"] - centers[target][0], detection["y"] - centers[target][1]) <= radius:
            remaining.remove(target)
            matched += 1
    return matched


def synthetic_benchmark(parameters, cfg):
    axis = np.linspace(-0.6, 0.6, 161)
    fluid = np.ones((axis.size, axis.size), dtype=bool)
    cases = []
    for separation in cfg["synthetic_separations"]:
        for translated in [False, True]:
            translation = tuple(cfg["synthetic_translation"]) if translated else (0.0, 0.0)
            u, v = synthetic_velocity(axis, separation=float(separation), translation=translation, shear=cfg["synthetic_shear"])
            fields = stage14.derive_fields(axis, axis, u, v)
            snapshot = {"x": axis, "y": axis, "fluid": fluid, **fields}
            raw = region_candidates(
                snapshot,
                parameters["seed_quantile"],
                cfg["low_region_quantile"],
                cfg["boundary_margin_cells"],
                cfg["refinement_half_width"],
            )
            detections = select_candidates(
                raw,
                parameters["minimum_source_support"],
                parameters["nms_radius"],
                10,
            )
            centers = [(-0.5 * float(separation), 0.0), (0.5 * float(separation), 0.0)]
            cases.append({
                "separation": float(separation),
                "translated": translated,
                "detections": len(detections),
                "matched_cores": _match_centers(detections, centers, cfg["synthetic_match_radius"]),
            })
    base = {row["separation"]: row for row in cases if not row["translated"]}
    moved = {row["separation"]: row for row in cases if row["translated"]}
    invariant = all(base[key]["matched_cores"] == moved[key]["matched_cores"] for key in base)
    resolved = [key for key, row in base.items() if row["matched_cores"] == 2 and moved[key]["matched_cores"] == 2]

    xx, yy = np.meshgrid(axis, axis, indexing="ij")
    pure_shear = {"x": axis, "y": axis, "fluid": fluid, **stage14.derive_fields(axis, axis, cfg["pure_shear_rate"] * yy, np.zeros_like(xx))}
    shear_raw = region_candidates(
        pure_shear,
        parameters["seed_quantile"],
        cfg["low_region_quantile"],
        cfg["boundary_margin_cells"],
        cfg["refinement_half_width"],
    )
    shear_detections = select_candidates(
        shear_raw, parameters["minimum_source_support"], parameters["nms_radius"], 10
    )
    return {
        "cases": cases,
        "translation_invariance_pass": invariant,
        "minimum_resolved_separation": min(resolved) if resolved else None,
        "pure_shear_false_positives": len(shear_detections),
        "pass": invariant and bool(resolved) and len(shear_detections) == 0,
    }


def draw_physical_comparison(path, snapshot, q_detections, hybrid_detections):
    import matplotlib.pyplot as plt

    masked = np.where(snapshot["fluid"], snapshot["omega"], np.nan)
    limit = max(float(np.nanpercentile(np.abs(masked), 99.5)), 1.0e-8)
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), sharex=True, sharey=True, constrained_layout=True)
    panels = ["Raw vorticity", "Criteria-derived reference", "Calibrated Q", "Region-deblended hybrid"]
    close = stage14.close_reference_members(snapshot["reference"], 0.18)
    for axis, label in zip(axes.flat, panels):
        axis.contourf(
            snapshot["x"], snapshot["y"], masked.T,
            levels=np.linspace(-limit, limit, 81), cmap="RdBu_r", extend="both"
        )
        axis.set_title(label)
        axis.set_aspect("equal")
        axis.set(xlabel="x", ylabel="y")
    reference = snapshot["reference"]
    axes[0, 1].scatter(
        [float(row["x_physical"]) for row in reference], [float(row["y_physical"]) for row in reference],
        marker="+", s=38, c="black", linewidths=1.2
    )
    if close:
        axes[0, 1].scatter(
            [float(reference[i]["x_physical"]) for i in sorted(close)],
            [float(reference[i]["y_physical"]) for i in sorted(close)],
            marker="s", s=60, facecolors="none", edgecolors="#b000b5", linewidths=1.2
        )
    axes[1, 0].scatter(
        [row["x"] for row in q_detections], [row["y"] for row in q_detections],
        s=54, facecolors="none", edgecolors="#ffd400", linewidths=1.5
    )
    axes[1, 1].scatter(
        [row["x"] for row in hybrid_detections], [row["y"] for row in hybrid_detections],
        s=54, facecolors="none", edgecolors="#00d5ff", linewidths=1.5
    )
    fig.savefig(path, dpi=240, bbox_inches="tight")
    plt.close(fig)


def main():
    global stage8, stage14
    stage8 = load_sibling("reference_catalogue_pgrrd", "run_dart_stage8_physics_catalogue.py")
    stage14 = load_sibling("baseline_tools_pgrrd", "run_vortex_stage14_baselines.py")
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--mfc-root", type=Path, required=True)
    parser.add_argument("--reference-catalogue", type=Path, required=True)
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--gamma-report", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads((args.config or ROOT / "vortex_pgrrd.json").read_text())
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    baseline_report = json.loads(args.baseline_report.read_text())
    gamma_report = json.loads(args.gamma_report.read_text())
    if gamma_report.get("claim_gate") != "gi_vgcm_benchmark_complete_not_superior_to_q":
        parser.error("Variable-Gamma terminal comparison gate is missing or unexpected")

    sys.path.insert(0, str(args.mfc_root.resolve() / "toolchain"))
    from mfc.viz.reader import assemble, discover_timesteps

    required = list(range(cfg["step_start"], cfg["step_stop"] + 1, cfg["step_stride"]))
    available = discover_timesteps(str(args.case_dir.resolve()), "binary")
    missing = sorted(set(required) - set(available))
    if missing:
        parser.error(f"raw MFC sequence incomplete: missing {len(missing)}; first={missing[0]}")
    references = {}
    with args.reference_catalogue.open(newline="") as stream:
        for row in csv.DictReader(stream):
            references.setdefault(int(row["source_step"]), []).append(row)

    quantiles = [float(value) for value in cfg["calibration_grid"]["seed_quantile"]]
    raw = {quantile: {} for quantile in quantiles}
    snapshots = {}
    q_detections = {}
    physical = {}
    q_parameters = baseline_report["selected_baseline_configurations"]["q"]
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
        fluid = (
            stage8.geometry_fluid_mask(x, y)
            & (x[:, None] >= cfg["analysis_xlim"][0]) & (x[:, None] <= cfg["analysis_xlim"][1])
            & (y[None, :] >= cfg["analysis_ylim"][0]) & (y[None, :] <= cfg["analysis_ylim"][1])
        )
        snapshot = {"x": x, "y": y, "fluid": fluid, "reference": references.get(step, []), "step": step, **fields}
        for quantile in quantiles:
            raw[quantile][frame] = region_candidates(
                snapshot, quantile, cfg["low_region_quantile"],
                cfg["boundary_margin_cells"], cfg["refinement_half_width"]
            )
        q_detections[frame] = stage14.baseline_detect(snapshot, "q", q_parameters)
        snapshots[frame] = {"reference": snapshot["reference"]}
        if frame in cfg["comparison_frames"]:
            physical[frame] = snapshot
        del assembled, u, v

    calibration = list(range(cfg["calibration_frames"][0], cfg["calibration_frames"][1] + 1))
    holdout = list(range(cfg["holdout_frames"][0], cfg["holdout_frames"][1] + 1))
    selected, sweep = calibrate(raw, snapshots, calibration, cfg)
    hybrid_metrics, _ = evaluate_configuration(raw, snapshots, holdout, selected, cfg)
    q_metrics, _ = stage14.evaluate(
        lambda frame: q_detections.get(frame, []), snapshots, holdout,
        cfg["reference_match_radius"], cfg["close_pair_maximum_separation"]
    )
    selected_by_frame = {
        frame: select_candidates(
            raw[float(selected["seed_quantile"])].get(frame, []),
            selected["minimum_source_support"], selected["nms_radius"], selected["maximum_detections"]
        )
        for frame in range(len(required))
    }
    detections = []
    for frame, rows in selected_by_frame.items():
        for row in rows:
            detections.append({"frame_index": frame, "source_step": required[frame], **row})
    write_csv(out / "pgrrd_detections.csv", detections)
    write_csv(out / "pgrrd_calibration_sweep.csv", sweep)
    synthetic = synthetic_benchmark(selected, cfg)
    write_csv(out / "pgrrd_synthetic_resolution.csv", synthetic.pop("cases"))
    for frame, snapshot in physical.items():
        draw_physical_comparison(
            out / f"pgrrd_physical_{frame:04d}.png", snapshot,
            q_detections.get(frame, []), selected_by_frame.get(frame, [])
        )

    q_expected = baseline_report["holdout_metrics"]["q"]
    gates = {
        "raw_sequence_complete": "pass",
        "variable_gamma_negative_result_consumed": "pass",
        "q_holdout_reproduced": "pass" if abs(q_metrics["coverage"] - q_expected["coverage"]) <= 1.0e-12 else "fail",
        "hybrid_candidate_control": "pass" if hybrid_metrics["detection_to_reference_ratio"] <= cfg["maximum_detection_to_reference_ratio"] else "fail",
        "hybrid_beats_q_coverage": "pass" if hybrid_metrics["coverage"] > q_metrics["coverage"] else "fail",
        "hybrid_beats_q_close_cores": "pass" if hybrid_metrics["close_member_coverage"] > q_metrics["close_member_coverage"] else "fail",
        "synthetic_resolution_and_invariance": "pass" if synthetic["pass"] else "fail",
    }
    superior = all(gates[key] == "pass" for key in [
        "hybrid_candidate_control", "hybrid_beats_q_coverage",
        "hybrid_beats_q_close_cores", "synthetic_resolution_and_invariance"
    ])
    report = {
        "schema_version": 1,
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision_context": {
            "variable_gamma_claim_gate": gamma_report["claim_gate"],
            "gi_vgcm_coverage": gamma_report["holdout_metrics"]["gi_vgcm"]["coverage"],
            "q_coverage_in_gamma_benchmark": gamma_report["holdout_metrics"]["q"]["coverage"],
        },
        "method_name": "Physics-Guided Rotating-Region Deblending (PG-RRD)",
        "method": "zero-inflation-safe rotating-region multi-peak deblending using Q, lambda_ci, and signed vorticity",
        "selected_configuration": selected,
        "holdout_metrics": {"hybrid": hybrid_metrics, "q": q_metrics},
        "synthetic_benchmark": synthetic,
        "gates": gates,
        "claim_gate": (
            "hybrid_candidate_for_independent_cross_case_validation"
            if superior else "q_retained_as_primary_detector_cross_case_validation_next"
        ),
        "limitations": [
            "The criteria-derived reference catalogue is non-exhaustive, so it is not independent ground truth.",
            "Only frames 1-30 were used for calibration; frames 31-60 are the fixed temporal holdout.",
            "Independent expert labels and a second physical flow case are required before publication precision and recall are claimed.",
            "The detector localizes two-dimensional vortex cores; it does not segment three-dimensional vortex tubes.",
        ],
    }
    (out / "pgrrd_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("PGRRD_STATUS=completed")
    print(f"PGRRD_HYBRID_COVERAGE={hybrid_metrics['coverage']:.6f}")
    print(f"PGRRD_HYBRID_CLOSE_COVERAGE={hybrid_metrics['close_member_coverage']:.6f}")
    print(f"PGRRD_Q_COVERAGE={q_metrics['coverage']:.6f}")
    print(f"PGRRD_MIN_SYNTHETIC_SEPARATION={synthetic['minimum_resolved_separation']}")
    print(f"PGRRD_CLAIM_GATE={report['claim_gate']}")
    print(f"PGRRD_REPORT={out / 'pgrrd_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
