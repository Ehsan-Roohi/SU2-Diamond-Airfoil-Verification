#!/usr/bin/env python3
"""Develop and temporally validate adaptive candidate budgeting for frozen CMCD/Q."""
from __future__ import annotations

import argparse
import csv
import importlib.util
import itertools
import json
import math
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


def all_q_candidates(snapshot: dict, frozen: dict) -> tuple[list[dict], dict]:
    """Return all robust-thresholded, same-sign-NMS Q maxima without a count cap."""
    # SciPy is available in the Unity analysis environment but intentionally
    # absent from the repository's lightweight static-test environment.
    from scipy.ndimage import maximum_filter

    score = snapshot["q"]
    fluid = snapshot["fluid"]
    finite = score[fluid & np.isfinite(score)]
    median = float(np.median(finite))
    mad = float(np.median(np.abs(finite - median)))
    threshold = median + float(frozen["snr"]) * 1.4826 * max(mad, 1.0e-12)
    peaks = (score == maximum_filter(score, size=3, mode="nearest")) & (score >= threshold) & fluid
    x, y = snapshot["x"], snapshot["y"]
    candidates: list[dict] = []
    for i, j in np.argwhere(peaks):
        if i < 2 or j < 2 or i >= len(x) - 2 or j >= len(y) - 2:
            continue
        candidates.append({
            "x": float(x[i]),
            "y": float(y[j]),
            "sign": 1 if snapshot["omega"][i, j] >= 0 else -1,
            "score": float(score[i, j]),
            "method": "acb_cmcd_q",
        })
    accepted: list[dict] = []
    radius = float(frozen["nms_radius"])
    for candidate in sorted(candidates, key=lambda row: -row["score"]):
        if any(
            candidate["sign"] == prior["sign"]
            and math.hypot(candidate["x"] - prior["x"], candidate["y"] - prior["y"]) < radius
            for prior in accepted
        ):
            continue
        accepted.append(candidate)
    return accepted, {"robust_q_threshold": threshold, "uncapped_candidates": len(accepted)}


def select_adaptive(candidates: list[dict], parameters: dict) -> tuple[list[dict], dict]:
    """Select a count at a robust score elbow, with a ratio-tail fallback and safety cap."""
    minimum = int(parameters["minimum_detections"])
    maximum = int(parameters["maximum_detections"])
    if len(candidates) <= minimum:
        return list(candidates), {"selected_count": len(candidates), "selection_reason": "all_below_minimum", "log_gap": None}
    upper = min(len(candidates), maximum)
    scores = np.asarray([max(float(row["score"]), 1.0e-300) for row in candidates[:upper]])
    gaps = np.log(scores[:-1]) - np.log(scores[1:])
    eligible = [(k, float(gaps[k - 1])) for k in range(minimum, upper)]
    best_k, best_gap = max(eligible, key=lambda item: item[1]) if eligible else (upper, 0.0)
    if best_gap >= float(parameters["minimum_log_score_gap"]):
        count = best_k
        reason = "score_elbow"
    else:
        anchor = scores[minimum - 1]
        floor = anchor * float(parameters["tail_score_fraction"])
        count = max(minimum, sum(float(row["score"]) >= floor for row in candidates[:upper]))
        reason = "tail_support"
    count = min(count, maximum, len(candidates))
    return list(candidates[:count]), {
        "selected_count": count,
        "selection_reason": reason,
        "log_gap": best_gap,
        "safety_cap_active": len(candidates) > maximum and count == maximum,
    }


def add_precision(metrics: dict) -> dict:
    result = dict(metrics)
    precision = result["matches"] / max(result["detections"], 1)
    recall = result["coverage"]
    result["precision_proxy"] = precision
    result["f1_proxy"] = 2.0 * precision * recall / max(precision + recall, 1.0e-300)
    return result


def selector_saturation(raw_by_frame: dict, selected_by_frame: dict, frames: list[int], maximum: int) -> float:
    active = sum(
        len(raw_by_frame[frame]) > maximum and len(selected_by_frame[frame]) == maximum
        for frame in frames
    )
    return active / max(len(frames), 1)


def draw_physical(path: Path, snapshot: dict, fixed: list[dict], adaptive: list[dict]) -> None:
    import matplotlib.pyplot as plt

    masked = np.where(snapshot["fluid"], snapshot["omega"], np.nan)
    limit = max(float(np.nanpercentile(np.abs(masked), 99.5)), 1.0e-8)
    levels = np.linspace(-limit, limit, 81)
    fig, axes = plt.subplots(1, 4, figsize=(20, 5.2), sharex=True, sharey=True, constrained_layout=True)
    labels = ["(a) Raw vorticity", "(b) Criteria reference", "(c) Fixed-budget CMCD", "(d) ACB-CMCD"]
    for axis, label in zip(axes, labels):
        axis.contourf(snapshot["x"], snapshot["y"], masked.T, levels=levels, cmap="RdBu_r", extend="both")
        axis.set_title(label)
        axis.set_aspect("equal")
        axis.set_xlabel("x/c")
    axes[0].set_ylabel("y/c")
    reference = snapshot["reference"]
    if reference:
        axes[1].scatter(
            [float(row["x_physical"]) for row in reference],
            [float(row["y_physical"]) for row in reference],
            marker="+", s=40, c="black", linewidths=1.2,
        )
    for axis, detections, color in [(axes[2], fixed, "#00bde3"), (axes[3], adaptive, "#ffe000")]:
        if detections:
            axis.scatter(
                [row["x"] for row in detections], [row["y"] for row in detections],
                s=54, facecolors="none", edgecolors=color, linewidths=1.5,
            )
    fig.savefig(path, dpi=250, bbox_inches="tight")
    plt.close(fig)


def draw_blind_crop(path: Path, snapshot: dict, x0: float, y0: float, half_width: float) -> None:
    import matplotlib.pyplot as plt

    x, y = snapshot["x"], snapshot["y"]
    ix = (x >= x0 - half_width) & (x <= x0 + half_width)
    iy = (y >= y0 - half_width) & (y <= y0 + half_width)
    field = np.where(snapshot["fluid"][np.ix_(ix, iy)], snapshot["omega"][np.ix_(ix, iy)], np.nan)
    limit = max(float(np.nanpercentile(np.abs(field), 99.0)), 1.0e-8)
    fig, axis = plt.subplots(figsize=(4.2, 4.2), constrained_layout=True)
    axis.contourf(x[ix] - x0, y[iy] - y0, field.T, levels=np.linspace(-limit, limit, 81), cmap="RdBu_r", extend="both")
    axis.plot(0, 0, "+", color="0.35", markersize=9, markeredgewidth=1)
    axis.set(xlabel=r"$\Delta x/c$", ylabel=r"$\Delta y/c$")
    axis.set_aspect("equal")
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def point_is_near(point: dict, rows: list[dict], radius: float, reference: bool = False) -> bool:
    x_key, y_key, sign_key = ("x_physical", "y_physical", "rotation_sign") if reference else ("x", "y", "sign")
    return any(
        int(point["sign"]) == int(row[sign_key])
        and math.hypot(float(point["x"]) - float(row[x_key]), float(point["y"]) - float(row[y_key])) <= radius
        for row in rows
    )


def main() -> int:
    reference_tools = load_sibling("acb_reference_tools", "run_dart_stage5_raw_reference.py")
    detector_tools = load_sibling("acb_detector_tools", "run_vortex_stage14_baselines.py")
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--mfc-root", type=Path, required=True)
    parser.add_argument("--ccfcv-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads((args.config or ROOT / "vortex_acb_cmcd.json").read_text())
    ccfcv_dir = args.ccfcv_dir.resolve()
    ccfcv = json.loads((ccfcv_dir / "ccfcv_report.json").read_text())
    expected_gate = "frozen_cmcd_transfers_to_alpha30_expert_labels_and_third_case_next"
    if ccfcv.get("claim_gate") != expected_gate:
        parser.error(f"CC-FCV prerequisite did not pass: {ccfcv.get('claim_gate')}")
    frozen = dict(ccfcv["frozen_cmcd_q_configuration"])
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    references_by_step: dict[int, list[dict]] = defaultdict(list)
    with (ccfcv_dir / "ccfcv_reference_catalogue.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            references_by_step[int(row["source_step"])].append(row)

    mfc_root = args.mfc_root.resolve()
    sys.path.insert(0, str(mfc_root / "toolchain"))
    from mfc.viz.reader import assemble, discover_timesteps

    required = list(range(cfg["step_start"], cfg["step_stop"] + 1, cfg["step_stride"]))
    available = discover_timesteps(str(args.case_dir.resolve()), "binary")
    missing = sorted(set(required) - set(available))
    if missing:
        parser.error(f"alpha-30 raw sequence incomplete: missing {len(missing)}; first={missing[0]}")

    snapshots: dict[int, dict] = {}
    raw_candidates: dict[int, list[dict]] = {}
    fixed_by_frame: dict[int, list[dict]] = {}
    threshold_rows: list[dict] = []
    for frame_index, step in enumerate(required):
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
        fields = detector_tools.derive_fields(x, y, u, v)
        fluid = (
            reference_tools.geometry_fluid_mask(x, y)
            & (x[:, None] >= cfg["analysis_xlim"][0]) & (x[:, None] <= cfg["analysis_xlim"][1])
            & (y[None, :] >= cfg["analysis_ylim"][0]) & (y[None, :] <= cfg["analysis_ylim"][1])
        )
        snapshot = {"x": x, "y": y, "fluid": fluid, "reference": references_by_step.get(step, []), "step": step, **fields}
        candidates, threshold = all_q_candidates(snapshot, frozen)
        snapshots[frame_index] = snapshot
        raw_candidates[frame_index] = candidates
        fixed_by_frame[frame_index] = candidates[: int(frozen["maximum_detections"])]
        threshold_rows.append({"frame_index": frame_index, "source_step": step, **threshold})
        del assembled, u, v

    calibration = list(range(cfg["calibration_frames"][0], cfg["calibration_frames"][1] + 1))
    holdout = list(range(cfg["temporal_holdout_frames"][0], cfg["temporal_holdout_frames"][1] + 1))
    keys = ["minimum_detections", "maximum_detections", "minimum_log_score_gap", "tail_score_fraction"]
    sweep_rows: list[dict] = []
    choices: list[tuple[dict, dict, dict]] = []
    for configuration_id, values in enumerate(itertools.product(*(cfg["selector_grid"][key] for key in keys))):
        parameters = dict(zip(keys, values))
        selected_by_frame = {frame: select_adaptive(raw_candidates[frame], parameters)[0] for frame in calibration}
        metrics, _ = detector_tools.evaluate(
            lambda frame, selected=selected_by_frame: selected[frame], snapshots, calibration,
            cfg["reference_match_radius"], cfg["close_pair_maximum_separation"],
        )
        metrics = add_precision(metrics)
        saturation = selector_saturation(raw_candidates, selected_by_frame, calibration, int(parameters["maximum_detections"]))
        excess = max(metrics["detection_to_reference_ratio"] - cfg["target_maximum_detection_to_reference_ratio"], 0.0)
        objective = (
            metrics["coverage"] + cfg["close_pair_objective_weight"] * metrics["close_member_coverage"]
            - cfg["candidate_excess_penalty"] * excess - cfg["safety_cap_penalty"] * saturation
        )
        row = {
            "configuration_id": configuration_id, **parameters, **metrics,
            "safety_cap_fraction": saturation, "objective": objective,
            "feasible": metrics["detection_to_reference_ratio"] <= cfg["target_maximum_detection_to_reference_ratio"]
            and saturation <= cfg["maximum_calibration_safety_cap_fraction"],
        }
        sweep_rows.append(row)
        choices.append((parameters, row, selected_by_frame))
    feasible = [choice for choice in choices if choice[1]["feasible"]]
    selected_parameters, calibration_metrics, _ = max(
        feasible or choices,
        key=lambda choice: (
            choice[1]["objective"], choice[1]["coverage"], choice[1]["close_member_coverage"],
            -choice[1]["detection_to_reference_ratio"],
        ),
    )

    adaptive_by_frame: dict[int, list[dict]] = {}
    selection_rows: list[dict] = []
    for frame in range(len(required)):
        selected, diagnostics = select_adaptive(raw_candidates[frame], selected_parameters)
        adaptive_by_frame[frame] = selected
        selection_rows.append({
            "frame_index": frame, "source_step": required[frame],
            "reference_cores": len(snapshots[frame]["reference"]),
            "fixed_detections": len(fixed_by_frame[frame]),
            "adaptive_detections": len(selected),
            "uncapped_candidates": len(raw_candidates[frame]), **diagnostics,
        })

    fixed_metrics, _ = detector_tools.evaluate(
        lambda frame: fixed_by_frame[frame], snapshots, holdout,
        cfg["reference_match_radius"], cfg["close_pair_maximum_separation"],
    )
    adaptive_metrics, _ = detector_tools.evaluate(
        lambda frame: adaptive_by_frame[frame], snapshots, holdout,
        cfg["reference_match_radius"], cfg["close_pair_maximum_separation"],
    )
    fixed_metrics = add_precision(fixed_metrics)
    adaptive_metrics = add_precision(adaptive_metrics)
    fixed_saturation = sum(len(raw_candidates[frame]) > int(frozen["maximum_detections"]) for frame in holdout) / len(holdout)
    adaptive_saturation = selector_saturation(
        raw_candidates, adaptive_by_frame, holdout, int(selected_parameters["maximum_detections"])
    )

    detection_rows: list[dict] = []
    for frame in range(len(required)):
        for rank, row in enumerate(adaptive_by_frame[frame], start=1):
            detection_rows.append({
                "frame_index": frame, "source_step": required[frame], "rank": rank,
                "x": row["x"], "y": row["y"], "rotation_sign": row["sign"], "q_score": row["score"],
            })
    for frame in cfg["physical_figure_frames"]:
        draw_physical(output / f"acb_cmcd_physical_{frame:04d}.png", snapshots[frame], fixed_by_frame[frame], adaptive_by_frame[frame])

    rng = np.random.default_rng(cfg["audit_seed"])
    audit_pools = {"adaptive_only": [], "shared": [], "reference_miss": []}
    for frame in holdout:
        fixed = fixed_by_frame[frame]
        adaptive = adaptive_by_frame[frame]
        reference = snapshots[frame]["reference"]
        for row in adaptive:
            category = "shared" if point_is_near(row, fixed, cfg["reference_match_radius"]) else "adaptive_only"
            audit_pools[category].append({"frame_index": frame, **row})
        for row in reference:
            point = {"x": float(row["x_physical"]), "y": float(row["y_physical"]), "sign": int(row["rotation_sign"])}
            if not point_is_near(point, adaptive, cfg["reference_match_radius"]):
                audit_pools["reference_miss"].append({"frame_index": frame, **point})
    audit_key: list[dict] = []
    audit_labels: list[dict] = []
    for category in ["adaptive_only", "shared", "reference_miss"]:
        pool = audit_pools[category]
        count = min(int(cfg["audit_samples_per_category"]), len(pool))
        sample = rng.choice(pool, size=count, replace=False).tolist() if count else []
        for row in sample:
            audit_id = f"B{len(audit_key) + 1:03d}"
            frame = int(row["frame_index"])
            draw_blind_crop(
                output / f"acb_cmcd_blind_{audit_id}.png", snapshots[frame],
                float(row["x"]), float(row["y"]), cfg["audit_crop_half_width"],
            )
            audit_key.append({
                "audit_id": audit_id, "hidden_category": category, "frame_index": frame,
                "source_step": required[frame], "x_physical": row["x"], "y_physical": row["y"],
                "rotation_sign": row["sign"],
            })
            audit_labels.append({
                "audit_id": audit_id, "is_vortex": "", "center_correct": "", "rotation_sign_correct": "",
                "confidence": "", "annotator": "", "notes": "",
            })

    coverage_gain = adaptive_metrics["coverage"] - fixed_metrics["coverage"]
    close_gain = adaptive_metrics["close_member_coverage"] - fixed_metrics["close_member_coverage"]
    f1_gain = adaptive_metrics["f1_proxy"] - fixed_metrics["f1_proxy"]
    gates = {
        "ccfcv_prerequisite": "pass",
        "frozen_physics_configuration": "pass",
        "temporal_split_without_holdout_tuning": "pass",
        "calibration_candidate_control": "pass" if bool(calibration_metrics["feasible"]) else "fail",
        "holdout_coverage_gain": "pass" if coverage_gain >= cfg["minimum_holdout_coverage_gain"] else "fail",
        "holdout_close_core_gain": "pass" if close_gain >= cfg["minimum_holdout_close_core_gain"] else "fail",
        "holdout_f1_not_worse": "pass" if f1_gain >= 0.0 else "fail",
        "holdout_candidate_control": "pass" if adaptive_metrics["detection_to_reference_ratio"] <= cfg["maximum_holdout_detection_to_reference_ratio"] else "fail",
        "blind_audit_pack": "pass" if len(audit_key) >= 24 else "fail",
    }
    scientific_pass = all(value == "pass" for value in gates.values())
    locked = {
        "schema_version": 1,
        "method_name": cfg["method_name"],
        "physics_configuration": {"criterion": "q", "snr": frozen["snr"], "nms_radius": frozen["nms_radius"]},
        "candidate_budget_configuration": selected_parameters,
        "development_case": cfg["case_id"],
        "configuration_status": "locked_for_third_case" if scientific_pass else "not_locked_fixed_budget_retained",
        "must_not_be_recalibrated_on_third_case": True,
    }
    (output / "acb_cmcd_locked_configuration.json").write_text(json.dumps(locked, indent=2) + "\n")
    write_csv(output / "acb_cmcd_selector_sweep.csv", sweep_rows, list(sweep_rows[0]))
    write_csv(output / "acb_cmcd_per_frame.csv", selection_rows, list(selection_rows[0]))
    write_csv(output / "acb_cmcd_detections.csv", detection_rows, list(detection_rows[0]))
    write_csv(output / "acb_cmcd_blind_key.csv", audit_key, list(audit_key[0]))
    write_csv(output / "acb_cmcd_expert_labels.csv", audit_labels, list(audit_labels[0]))
    write_csv(output / "acb_cmcd_thresholds.csv", threshold_rows, list(threshold_rows[0]))

    report = {
        "schema_version": 1,
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_name": cfg["method_name"],
        "case_id": cfg["case_id"],
        "development_protocol": {
            "calibration_frames": calibration,
            "temporal_holdout_frames": holdout,
            "physics_parameters_recalibrated": False,
            "candidate_budget_only_calibrated": True,
        },
        "frozen_fixed_budget_configuration": frozen,
        "selected_adaptive_configuration": selected_parameters,
        "calibration_metrics": calibration_metrics,
        "temporal_holdout": {
            "fixed_budget": fixed_metrics,
            "adaptive_budget": adaptive_metrics,
            "coverage_gain": coverage_gain,
            "close_member_coverage_gain": close_gain,
            "f1_proxy_gain": f1_gain,
            "fixed_cap_active_fraction": fixed_saturation,
            "adaptive_safety_cap_active_fraction": adaptive_saturation,
        },
        "blind_audit_samples": len(audit_key),
        "gates": gates,
        "claim_gate": "acb_cmcd_locked_for_third_case_blind_validation" if scientific_pass else "fixed_budget_cmcd_retained_no_adaptive_holdout_gain",
        "limitations": [
            "Alpha-30 is now a development case and cannot be reused as the final blind validation case.",
            "The criteria catalogue is a physics-derived proxy, not independent expert ground truth.",
            "Precision and recall remain provisional until the supplied blinded crops are independently labelled.",
            "The next scientific test must use the locked configuration on a third flow topology without recalibration.",
            "This method localizes two-dimensional vortex cores; it does not segment three-dimensional vortex tubes."
        ],
    }
    (output / "acb_cmcd_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("ACB_CMCD_STATUS=completed")
    print(f"ACB_CMCD_FIXED_HOLDOUT_COVERAGE={fixed_metrics['coverage']:.8f}")
    print(f"ACB_CMCD_ADAPTIVE_HOLDOUT_COVERAGE={adaptive_metrics['coverage']:.8f}")
    print(f"ACB_CMCD_FIXED_CLOSE_COVERAGE={fixed_metrics['close_member_coverage']:.8f}")
    print(f"ACB_CMCD_ADAPTIVE_CLOSE_COVERAGE={adaptive_metrics['close_member_coverage']:.8f}")
    print(f"ACB_CMCD_ADAPTIVE_RATIO={adaptive_metrics['detection_to_reference_ratio']:.8f}")
    print(f"ACB_CMCD_AUDIT_SAMPLES={len(audit_key)}")
    print(f"ACB_CMCD_CLAIM_GATE={report['claim_gate']}")
    print(f"ACB_CMCD_REPORT={output / 'acb_cmcd_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
