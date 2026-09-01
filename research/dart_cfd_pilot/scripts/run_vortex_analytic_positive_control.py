#!/usr/bin/env python3
"""Frozen analytic/adversarial positive controls for SRA-CMCD."""
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


def lamb_oseen(
    xx: np.ndarray,
    yy: np.ndarray,
    x0: float,
    y0: float,
    circulation: float,
    core_radius: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    dx, dy = xx - x0, yy - y0
    radius2 = dx * dx + dy * dy
    factor = circulation / (2.0 * math.pi) * (1.0 - np.exp(-radius2 / core_radius**2))
    factor = np.divide(factor, radius2, out=np.zeros_like(radius2), where=radius2 > 1.0e-15)
    u = -factor * dy
    v = factor * dx
    pressure_drop = 0.12 * (abs(circulation) / 0.8) ** 2 * np.exp(-radius2 / core_radius**2)
    return u, v, pressure_drop


def synthetic_case(case: dict, cfg: dict) -> dict:
    xmin, xmax, ymin, ymax = map(float, cfg["domain"])
    spacing = float(cfg["grid_spacing"])
    x = np.arange(xmin, xmax + 0.5 * spacing, spacing)
    y = np.arange(ymin, ymax + 0.5 * spacing, spacing)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    u = np.full_like(xx, 0.25)
    v = np.zeros_like(xx)
    pressure = np.ones_like(xx)
    density = np.ones_like(xx)
    truth: list[dict] = []
    for vortex in case.get("vortices", []):
        du, dv, drop = lamb_oseen(
            xx, yy, float(vortex["x"]), float(vortex["y"]),
            float(vortex["circulation"]), float(vortex["core_radius"]),
        )
        u += du
        v += dv
        pressure -= drop
        truth.append({
            "x": float(vortex["x"]), "y": float(vortex["y"]),
            "sign": 1 if float(vortex["circulation"]) > 0.0 else -1,
        })
    pressure = np.maximum(pressure, 0.2)
    density = pressure ** (1.0 / float(cfg["gamma"]))

    shock = case.get("shock")
    if shock:
        x0 = float(shock.get("x", 0.0))
        width = float(shock.get("width", 0.015))
        transition = 0.5 * (1.0 + np.tanh((xx - x0) / width))
        u -= float(shock.get("velocity_drop", 0.22)) * transition
        pressure += float(shock.get("pressure_jump", 0.6)) * transition
        density += float(shock.get("density_jump", 0.22)) * transition
        if shock.get("beads"):
            envelope = np.exp(-((xx - x0) / (2.2 * width)) ** 2)
            wavelength = float(shock.get("bead_wavelength", 0.12))
            v += 0.055 * envelope * np.sin(2.0 * math.pi * yy / wavelength)

    if case.get("kind") == "shear_layer":
        u += 0.7 * np.tanh(yy / 0.055)
    elif case.get("kind") == "pure_strain":
        u += 0.6 * xx
        v -= 0.6 * yy

    noise = float(case.get("noise", 0.0))
    if noise > 0.0:
        rng = np.random.default_rng(int(case.get("seed", 20260901)))
        u += noise * rng.standard_normal(u.shape)
        v += noise * rng.standard_normal(v.shape)
    return {
        "case_id": case["case_id"], "category": case["category"],
        "x": x, "y": y, "u": u, "v": v, "pressure": pressure,
        "rho": density, "fluid": np.ones_like(xx, dtype=bool), "truth": truth,
        "metadata": {k: value for k, value in case.items() if k not in {"vortices"}},
    }


def case_definitions(cfg: dict) -> list[dict]:
    core = float(cfg["vortex_core_radius"])
    circulation = float(cfg["vortex_circulation"])
    cases: list[dict] = []
    for sign in (-1, 1):
        for noise in cfg["velocity_noise_levels"]:
            cases.append({
                "case_id": f"isolated_s{sign:+d}_noise{float(noise):.3f}",
                "category": "isolated", "noise": float(noise), "seed": 20260901 + sign,
                "vortices": [{"x": 0.0, "y": 0.0, "circulation": sign * circulation, "core_radius": core}],
            })
    for pair_type, signs in (("co", (1, 1)), ("counter", (1, -1))):
        for separation in cfg["close_pair_separations"]:
            d = float(separation)
            cases.append({
                "case_id": f"{pair_type}_pair_d{d:.3f}", "category": f"{pair_type}_pair",
                "separation": d,
                "vortices": [
                    {"x": -0.5 * d, "y": 0.0, "circulation": signs[0] * circulation, "core_radius": core},
                    {"x": 0.5 * d, "y": 0.0, "circulation": signs[1] * circulation, "core_radius": core},
                ],
            })
    for offset in cfg["shock_vortex_offsets"]:
        d = float(offset)
        cases.append({
            "case_id": f"shock_vortex_d{d:.3f}", "category": "shock_vortex", "shock_offset": d,
            "shock": {"x": 0.0, "width": 0.015},
            "vortices": [{"x": -d, "y": 0.0, "circulation": circulation, "core_radius": core}],
        })
    cases.extend([
        {"case_id": "negative_pure_shear", "category": "negative", "kind": "shear_layer"},
        {"case_id": "negative_pure_strain", "category": "negative", "kind": "pure_strain"},
        {"case_id": "negative_planar_shock", "category": "negative", "shock": {"x": 0.0, "width": 0.015}},
        {"case_id": "negative_shock_beads", "category": "negative", "shock": {"x": 0.0, "width": 0.015, "beads": True}},
    ])
    return cases


def detect(snapshot: dict, cfg: dict, sra_cfg: dict, modules: dict) -> tuple[list[dict], dict]:
    artifact = modules["artifact"]
    base = modules["base"]
    sra = modules["sra"]
    fields = artifact.derive_artifact_fields(
        snapshot["x"], snapshot["y"], snapshot["u"], snapshot["v"],
        snapshot["fluid"], cfg["gaussian_sigmas"],
    )
    fields = sra.finish_raster_fields(
        snapshot["x"], snapshot["y"],
        {**fields, "pressure": snapshot["pressure"], "rho": snapshot["rho"]},
        snapshot["fluid"], cfg["gaussian_sigmas"], float(cfg["gamma"]),
    )
    work = {**snapshot, **fields}
    shock_mask, shock_distance = sra.build_shock_ridge_mask(work, sra_cfg)
    work["shock_ridge_mask"] = shock_mask
    raw, threshold = base.all_q_candidates(work, cfg["base_physics_configuration"])
    artifact_valid, artifact_audit = artifact.filter_candidates(
        raw, work, cfg["artifact_veto_configuration"]
    )
    selected, selection = base.select_adaptive(
        artifact_valid, cfg["candidate_budget_configuration"]
    )
    audit: list[dict] = []
    final: list[dict] = []
    for candidate in selected:
        rings = [
            sra.ring_winding_features(work, candidate, float(radius), int(sra_cfg["winding_samples"]))
            for radius in sra_cfg["winding_radii_cells"]
        ]
        winding_support = sum(sra.winding_pass(row, sra_cfg) for row in rings)
        island = sra.closed_q_island(work, candidate, sra_cfg)
        pressure = sra.pressure_core_support(work, candidate, sra_cfg)
        i, j = int(candidate["grid_i"]), int(candidate["grid_j"])
        distance = float(shock_distance[i, j])
        accepted, reason = sra.revised_decision(island, winding_support, pressure, distance, sra_cfg)
        row = {
            **candidate, "winding_support": winding_support,
            "q_island": island,
            "q_island_pass": island["pass"],
            "q_island_closed": island["closed"],
            "q_island_area_cells": island["area_cells"],
            "q_island_aspect_ratio": island["aspect_ratio"],
            "q_island_analysis_radius_cells": island["analysis_radius_cells"],
            "pressure_core": pressure, "pressure_pass": pressure["pass"],
            "shock_ridge_distance_cells": distance, "accepted": accepted,
            "rejection_reason": reason,
        }
        audit.append(row)
    sra.rescue_corroborated_opposite_sign_pairs(audit, sra_cfg)
    sra.suppress_subordinate_same_sign_peaks(audit, sra_cfg)
    final = [row for row in audit if row["accepted"]]
    diagnostics = {
        "raw_q_candidates": len(raw), "artifact_valid_candidates": len(artifact_valid),
        "selected_candidates": len(selected), "final_detections": len(final),
        "threshold": threshold, "selection": selection,
        "artifact_rejections": dict(Counter(row["artifact_reason"] for row in artifact_audit if not row["artifact_accepted"])),
        "sra_rejections": dict(Counter(row["rejection_reason"] for row in audit if not row["accepted"])),
    }
    return final, {"snapshot": work, "audit": audit, "diagnostics": diagnostics}


def score(truth: list[dict], detections: list[dict], radius: float) -> dict:
    spatial_pairs = sorted(
        (
            math.hypot(float(t["x"]) - float(d["x"]), float(t["y"]) - float(d["y"])), ti, di
        )
        for ti, t in enumerate(truth)
        for di, d in enumerate(detections)
    )
    spatial_truth: set[int] = set()
    spatial_detection: set[int] = set()
    correct_sign = 0
    for distance, ti, di in spatial_pairs:
        if distance > radius or ti in spatial_truth or di in spatial_detection:
            continue
        spatial_truth.add(ti)
        spatial_detection.add(di)
        correct_sign += int(int(truth[ti]["sign"]) == int(detections[di]["sign"]))
    pairs = sorted(
        (
            math.hypot(float(t["x"]) - float(d["x"]), float(t["y"]) - float(d["y"])), ti, di
        )
        for ti, t in enumerate(truth)
        for di, d in enumerate(detections)
        if int(t["sign"]) == int(d["sign"])
    )
    used_truth: set[int] = set()
    used_detection: set[int] = set()
    distances: list[float] = []
    for distance, ti, di in pairs:
        if distance > radius or ti in used_truth or di in used_detection:
            continue
        used_truth.add(ti)
        used_detection.add(di)
        distances.append(distance)
    tp = len(distances)
    return {
        "truth_count": len(truth), "detection_count": len(detections), "true_positive": tp,
        "false_positive": len(detections) - tp, "false_negative": len(truth) - tp,
        "recall": tp / max(len(truth), 1),
        "precision": tp / max(len(detections), 1),
        "localization_rmse": math.sqrt(sum(value * value for value in distances) / max(tp, 1)),
        "rotation_sign_accuracy": correct_sign / max(len(spatial_detection), 1),
    }


def draw_cases(path: Path, results: list[dict], case_ids: list[str], title: str) -> None:
    import matplotlib.pyplot as plt

    chosen = [next(row for row in results if row["case_id"] == case_id) for case_id in case_ids]
    columns = min(4, len(chosen))
    rows = math.ceil(len(chosen) / columns)
    fig, axes = plt.subplots(rows, columns, figsize=(4.4 * columns, 4.1 * rows), constrained_layout=True)
    axes = np.atleast_1d(axes).ravel()
    for axis, result in zip(axes, chosen):
        snapshot = result["runtime"]["snapshot"]
        field = snapshot["omega"]
        limit = max(float(np.percentile(np.abs(field), 99.5)), 1.0e-8)
        axis.contourf(snapshot["x"], snapshot["y"], field.T, levels=np.linspace(-limit, limit, 81), cmap="RdBu_r", extend="both")
        if np.any(snapshot["shock_ridge_mask"]):
            axis.contour(snapshot["x"], snapshot["y"], snapshot["shock_ridge_mask"].T.astype(float), levels=[0.5], colors="#c000ff", linewidths=1.0)
        truth = result["truth"]
        if truth:
            axis.scatter([r["x"] for r in truth], [r["y"] for r in truth], marker="+", s=95, c="black", linewidths=2.0, label="truth")
        detections = result["detections"]
        if detections:
            axis.scatter([r["x"] for r in detections], [r["y"] for r in detections], s=78, facecolors="none", edgecolors="#00e070", linewidths=2.0, label="SRA-CMCD")
        axis.set_title(f"{result['case_id']}\nTP={result['metrics']['true_positive']} FP={result['metrics']['false_positive']}")
        axis.set_aspect("equal")
        axis.set(xlim=(-0.42, 0.42), ylim=(-0.42, 0.42), xlabel="x/c", ylabel="y/c")
    for axis in axes[len(chosen):]:
        axis.remove()
    fig.suptitle(title, fontsize=15)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def aggregate(results: list[dict], cfg: dict) -> tuple[dict, dict]:
    by_category: dict[str, list[dict]] = defaultdict(list)
    for row in results:
        by_category[row["category"]].append(row)
    def totals(rows: list[dict]) -> dict:
        truth = sum(row["metrics"]["truth_count"] for row in rows)
        detections = sum(row["metrics"]["detection_count"] for row in rows)
        tp = sum(row["metrics"]["true_positive"] for row in rows)
        fp = sum(row["metrics"]["false_positive"] for row in rows)
        return {"cases": len(rows), "truth": truth, "detections": detections, "true_positive": tp, "false_positive": fp, "recall": tp / max(truth, 1), "precision": tp / max(detections, 1)}
    summary = {name: totals(rows) for name, rows in by_category.items()}
    isolated = summary.get("isolated", {})
    close_rows = [row for row in results if row["category"] in {"co_pair", "counter_pair"} and float(row["metadata"]["separation"]) >= float(cfg["acceptance_gates"]["minimum_clean_close_pair_separation"])]
    far_shock = [row for row in results if row["category"] == "shock_vortex" and float(row["metadata"]["shock_offset"]) > float(cfg["acceptance_gates"]["minimum_clean_close_pair_separation"])]
    negative_fp = sum(row["metrics"]["false_positive"] for row in by_category.get("negative", []))
    sign_values = [row["metrics"]["rotation_sign_accuracy"] for row in results if row["metrics"]["true_positive"]]
    close_case_recalls = [row["metrics"]["recall"] for row in close_rows]
    gates = {
        "isolated_vortex_recall": "pass" if isolated.get("recall", 0.0) >= float(cfg["acceptance_gates"]["minimum_isolated_recall"]) else "fail",
        "isolated_vortex_precision": "pass" if isolated.get("precision", 0.0) >= float(cfg["acceptance_gates"]["minimum_isolated_precision"]) else "fail",
        "resolved_close_pair_recall": "pass" if min(close_case_recalls or [0.0]) >= float(cfg["acceptance_gates"]["minimum_close_pair_member_recall"]) else "fail",
        "far_from_shock_recall": "pass" if totals(far_shock)["recall"] >= float(cfg["acceptance_gates"]["minimum_far_from_shock_recall"]) else "fail",
        "negative_control_false_positives": "pass" if negative_fp <= int(cfg["acceptance_gates"]["maximum_negative_control_false_positives"]) else "fail",
        "rotation_sign_accuracy": "pass" if min(sign_values or [0.0]) >= float(cfg["acceptance_gates"]["minimum_rotation_sign_accuracy"]) else "fail",
    }
    return summary, gates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--sra-config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads((args.config or ROOT / "vortex_analytic_positive_control.json").read_text())
    sra_cfg = json.loads((args.sra_config or ROOT / "vortex_shock_ridge_aware_cmcd.json").read_text())
    if cfg.get("future_case_recalibration_allowed") is not False:
        parser.error("analytic benchmark is not frozen")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    modules = {
        "base": load_sibling("analytic_base", "run_vortex_acb_cmcd.py"),
        "artifact": load_sibling("analytic_artifact", "run_vortex_artifact_aware_acb.py"),
        "sra": load_sibling("analytic_sra", "run_vortex_shock_ridge_aware_su2.py"),
    }
    results: list[dict] = []
    rows: list[dict] = []
    for definition in case_definitions(cfg):
        snapshot = synthetic_case(definition, cfg)
        detections, runtime = detect(snapshot, cfg, sra_cfg, modules)
        metrics = score(snapshot["truth"], detections, float(cfg["ground_truth_match_radius"]))
        result = {"case_id": snapshot["case_id"], "category": snapshot["category"], "metadata": snapshot["metadata"], "truth": snapshot["truth"], "detections": detections, "metrics": metrics, "runtime": runtime}
        results.append(result)
        rows.append({"case_id": result["case_id"], "category": result["category"], **result["metadata"], **runtime["diagnostics"], **metrics})
    summary, gates = aggregate(results, cfg)
    pair_ids = [row["case_id"] for row in results if row["category"] in {"co_pair", "counter_pair"} and row["metadata"]["separation"] in {0.06, 0.1, 0.12, 0.16}]
    shock_ids = [row["case_id"] for row in results if row["category"] == "shock_vortex"]
    isolated_ids = [row["case_id"] for row in results if row["category"] == "isolated"]
    draw_cases(output / "analytic_pc_isolated_physical.png", results, isolated_ids, "Isolated Lamb-Oseen positive controls")
    draw_cases(output / "analytic_pc_close_pairs_physical.png", results, pair_ids, "Close-core resolution: truth (+), detections (green)")
    draw_cases(output / "analytic_pc_shock_vortex_physical.png", results, shock_ids, "Shock-vortex exclusion-zone characterization")
    negative_ids = [row["case_id"] for row in results if row["category"] == "negative"]
    draw_cases(output / "analytic_pc_negative_controls_physical.png", results, negative_ids, "Adversarial negative controls")
    serializable = [{k: value for k, value in row.items() if k != "runtime"} for row in results]
    report = {
        "schema_version": 1, "status": "completed", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_name": cfg["method_name"], "protocol": cfg, "cases": serializable,
        "category_summary": summary, "gates": gates,
        "claim_gate": "analytic_positive_control_pass" if all(value == "pass" for value in gates.values()) else "analytic_positive_control_failed",
        "limitations": [
            "Analytic vortices are characterization controls, not independent CFD validation.",
            "The shock-vortex sweep measures the frozen veto exclusion zone and is not used to retune it.",
            "Publication validation still requires blinded time-resolved cylinder and additional-airfoil cases.",
        ],
    }
    (output / "analytic_positive_control_report.json").write_text(json.dumps(report, indent=2) + "\n")
    write_csv(output / "analytic_positive_control_cases.csv", rows, sorted({key for row in rows for key in row}))
    print("ANALYTIC_PC_STATUS=completed")
    print(f"ANALYTIC_PC_CLAIM_GATE={report['claim_gate']}")
    print(f"ANALYTIC_PC_GATES={json.dumps(gates, sort_keys=True)}")
    print(f"ANALYTIC_PC_REPORT={output / 'analytic_positive_control_report.json'}")
    return 0 if report["claim_gate"] == "analytic_positive_control_pass" else 5


if __name__ == "__main__":
    raise SystemExit(main())
