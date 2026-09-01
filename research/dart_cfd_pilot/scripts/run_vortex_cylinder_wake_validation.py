#!/usr/bin/env python3
"""Run a viscous cylinder wake and cross-validate frozen SRA-CMCD."""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import ndimage, signal


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent


def load_sibling(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def compile_solver(source: Path, binary: Path) -> None:
    command = [
        os.environ.get("CC", "cc"),
        "-O3", "-fopenmp", "-std=c11", "-Wall", "-Wextra", "-Werror",
        str(source), "-lm", "-o", str(binary),
    ]
    subprocess.run(command, check=True)


def run_solver(binary: Path, simulation: Path, cfg: dict, log: Path) -> None:
    solver = cfg["solver"]
    command = [
        str(binary),
        str(solver["nx"]), str(solver["ny"]), str(solver["diameter_cells"]),
        str(solver["reynolds_number"]), str(solver["inlet_lattice_velocity"]),
        str(solver["total_steps"]), str(solver["sample_start_step"]),
        str(solver["snapshot_stride"]), str(solver["monitor_stride"]),
        str(simulation),
    ]
    with log.open("w") as stream:
        subprocess.run(command, check=True, stdout=stream, stderr=subprocess.STDOUT)


def read_snapshot(path: Path) -> tuple[int, np.ndarray, np.ndarray, np.ndarray]:
    with path.open("rb") as stream:
        header = np.fromfile(stream, dtype=np.uint32, count=3)
        if header.size != 3:
            raise RuntimeError(f"invalid snapshot header: {path}")
        nx, ny, step = map(int, header)
        values = np.fromfile(stream, dtype=np.float32)
    expected = 3 * nx * ny
    if values.size != expected:
        raise RuntimeError(f"invalid snapshot payload: {path}; {values.size} != {expected}")
    fields = values.reshape(3, nx, ny).astype(np.float64)
    return step, fields[0], fields[1], fields[2]


def cylinder_coordinates(cfg: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    solver = cfg["solver"]
    nx, ny = int(solver["nx"]), int(solver["ny"])
    diameter = float(solver["diameter_cells"])
    cylinder_x = float(solver["cylinder_x_diameters_from_inlet"]) * diameter
    cylinder_y = 0.5 * (ny - 1)
    x = (np.arange(nx, dtype=float) - cylinder_x) / diameter
    y = (np.arange(ny, dtype=float) - cylinder_y) / diameter
    xx, yy = np.meshgrid(x, y, indexing="ij")
    fluid = xx * xx + yy * yy > 0.25
    return x, y, fluid


def shifted(array: np.ndarray, dx: int, dy: int, fill: float) -> np.ndarray:
    result = np.empty_like(array)
    if dx > 0:
        result[:-dx, :] = array[dx:, :]
        result[-dx:, :] = fill
    elif dx < 0:
        result[-dx:, :] = array[:dx, :]
        result[:-dx, :] = fill
    else:
        result[:] = array
    return np.roll(result, -dy, axis=1)


def gamma2_field(u: np.ndarray, v: np.ndarray, fluid: np.ndarray, radius: int) -> np.ndarray:
    size = 2 * radius + 1
    weights = ndimage.uniform_filter(
        fluid.astype(float), size=size, mode=("nearest", "wrap")
    )
    u_mean = ndimage.uniform_filter(
        np.where(fluid, u, 0.0), size=size, mode=("nearest", "wrap")
    ) / np.maximum(weights, 1.0e-12)
    v_mean = ndimage.uniform_filter(
        np.where(fluid, v, 0.0), size=size, mode=("nearest", "wrap")
    ) / np.maximum(weights, 1.0e-12)
    total = np.zeros_like(u)
    counts = np.zeros_like(u)
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            neighbor_u = shifted(u, dx, dy, np.nan)
            neighbor_v = shifted(v, dx, dy, np.nan)
            neighbor_fluid = shifted(fluid, dx, dy, 0).astype(bool)
            du = neighbor_u - u_mean
            dv = neighbor_v - v_mean
            denominator = math.hypot(dx, dy) * np.hypot(du, dv)
            valid = fluid & neighbor_fluid & np.isfinite(denominator) & (denominator > 1.0e-14)
            contribution = np.zeros_like(u)
            contribution[valid] = (dx * dv[valid] - dy * du[valid]) / denominator[valid]
            total += contribution
            counts += valid
    gamma2 = np.divide(total, counts, out=np.zeros_like(total), where=counts > 0.5)
    gamma2[~fluid] = np.nan
    return gamma2


def vorticity(x: np.ndarray, y: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    _, du_dy = np.gradient(u, x, y, edge_order=2)
    dv_dx, _ = np.gradient(v, x, y, edge_order=2)
    return dv_dx - du_dy


def reference_centers(
    gamma2: np.ndarray,
    omega: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    fluid: np.ndarray,
    cfg: dict,
) -> list[dict]:
    evaluation = cfg["evaluation"]
    threshold = float(evaluation["gamma2_threshold"])
    minimum_area = int(evaluation["gamma2_minimum_component_cells"])
    xmin, xmax = map(float, evaluation["wake_x_over_d"])
    ymin, ymax = map(float, evaluation["wake_y_over_d"])
    xx, yy = np.meshgrid(x, y, indexing="ij")
    region = fluid & (xx >= xmin) & (xx <= xmax) & (yy >= ymin) & (yy <= ymax)
    margin = int(evaluation.get(
        "reference_boundary_margin_cells", int(evaluation["gamma2_radius_cells"]) + 2
    ))
    boundary_safe = np.zeros_like(region)
    if len(x) > 2 * margin and len(y) > 2 * margin:
        boundary_safe[margin:-margin, margin:-margin] = True
    region &= boundary_safe
    rows: list[dict] = []
    structure = np.ones((3, 3), dtype=np.uint8)
    for polarity in (-1, 1):
        mask = region & np.isfinite(gamma2) & (polarity * gamma2 >= threshold)
        labels, components = ndimage.label(mask, structure=structure)
        for component in range(1, components + 1):
            indices = np.flatnonzero(labels == component)
            if indices.size < minimum_area:
                continue
            values = np.abs(gamma2.ravel()[indices])
            flat = int(indices[int(np.argmax(values))])
            i, j = np.unravel_index(flat, gamma2.shape)
            sign = 1 if float(omega[i, j]) >= 0.0 else -1
            rows.append({
                "x": float(x[i]), "y": float(y[j]), "sign": sign,
                "gamma2": float(gamma2[i, j]), "component_cells": int(indices.size),
            })
    return rows


def match_frame(reference: list[dict], detections: list[dict], radius: float) -> dict:
    pairs = sorted(
        (
            math.hypot(float(truth["x"]) - float(detection["x"]),
                       float(truth["y"]) - float(detection["y"])),
            truth_index, detection_index,
        )
        for truth_index, truth in enumerate(reference)
        for detection_index, detection in enumerate(detections)
    )
    used_reference: set[int] = set()
    used_detection: set[int] = set()
    distances: list[float] = []
    correct_sign = 0
    for distance, truth_index, detection_index in pairs:
        if distance > radius or truth_index in used_reference or detection_index in used_detection:
            continue
        used_reference.add(truth_index)
        used_detection.add(detection_index)
        distances.append(distance)
        correct_sign += int(
            int(reference[truth_index]["sign"]) == int(detections[detection_index]["sign"])
        )
    true_positive = len(distances)
    return {
        "reference_count": len(reference),
        "detection_count": len(detections),
        "true_positive": true_positive,
        "false_positive": len(detections) - true_positive,
        "false_negative": len(reference) - true_positive,
        "correct_rotation_sign": correct_sign,
        "localization_squared_error": sum(value * value for value in distances),
    }


def strouhal_metrics(monitor_path: Path, cfg: dict) -> dict:
    rows = np.genfromtxt(monitor_path, delimiter=",", names=True)
    start = int(cfg["evaluation"]["frequency_start_step"])
    keep = rows["step"] >= start
    steps = np.asarray(rows["step"][keep], dtype=float)
    transverse = signal.detrend(np.asarray(rows["probe_v"][keep], dtype=float))
    if steps.size < 64 or float(np.std(transverse)) < 1.0e-10:
        raise RuntimeError("wake-probe signal is insufficient for frequency estimation")
    sample_interval = float(np.median(np.diff(steps)))
    nfft = 1 << int(math.ceil(math.log2(8 * len(transverse))))
    frequency, power = signal.periodogram(
        transverse, fs=1.0 / sample_interval, window="hann", nfft=nfft,
        detrend=False, scaling="spectrum",
    )
    solver = cfg["solver"]
    diameter = float(solver["diameter_cells"])
    inlet_u = float(solver["inlet_lattice_velocity"])
    strouhal = frequency * diameter / inlet_u
    valid = (strouhal >= 0.05) & (strouhal <= 0.35)
    peak = np.flatnonzero(valid)[int(np.argmax(power[valid]))]
    peak_frequency = float(frequency[peak])
    spectral_strouhal = peak_frequency * diameter / inlet_u
    minimum_peak_distance = max(1, int(0.55 * diameter / (0.20 * inlet_u) / sample_interval))
    peaks, _ = signal.find_peaks(
        transverse, distance=minimum_peak_distance,
        prominence=max(0.15 * float(np.std(transverse)), 1.0e-12),
    )
    periods = np.diff(steps[peaks])
    peak_strouhal = (
        diameter / (inlet_u * float(np.median(periods))) if periods.size >= 2 else None
    )
    density_min = float(np.min(rows["rho_min"][keep]))
    density_max = float(np.max(rows["rho_max"][keep]))
    return {
        "samples": int(steps.size),
        "sample_interval_steps": sample_interval,
        "probe_rms": float(np.sqrt(np.mean(transverse * transverse))),
        "spectral_frequency_per_step": peak_frequency,
        "spectral_strouhal": spectral_strouhal,
        "peak_count": int(peaks.size),
        "peak_period_steps": float(np.median(periods)) if periods.size else None,
        "peak_strouhal": peak_strouhal,
        "density_min": density_min,
        "density_max": density_max,
        "maximum_density_deviation": max(abs(density_min - 1.0), abs(density_max - 1.0)),
        "steps": steps,
        "signal": transverse,
        "frequency": frequency,
        "power": power,
        "strouhal_axis": strouhal,
    }


def draw_physical(path: Path, chosen: list[dict], cfg: dict, detector_name: str) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle

    fig, axes = plt.subplots(
        len(chosen), 1, figsize=(15.5, 4.2 * len(chosen)), constrained_layout=True
    )
    axes = np.atleast_1d(axes)
    for axis, row in zip(axes, chosen):
        field = np.where(row["fluid"], row["omega"], np.nan)
        limit = max(float(np.nanpercentile(np.abs(field), 99.4)), 1.0e-8)
        axis.contourf(
            row["x"], row["y"], field.T,
            levels=np.linspace(-limit, limit, 101), cmap="RdBu_r", extend="both",
        )
        axis.add_patch(Circle((0.0, 0.0), 0.5, color="black", zorder=5))
        if row["reference"]:
            axis.scatter(
                [item["x"] for item in row["reference"]],
                [item["y"] for item in row["reference"]],
                marker="+", s=95, c="black", linewidths=1.8, label=r"independent $\Gamma_2$",
            )
        if row["detections"]:
            axis.scatter(
                [item["x"] for item in row["detections"]],
                [item["y"] for item in row["detections"]],
                s=78, facecolors="none", edgecolors="#00e070", linewidths=2.0,
                label=f"frozen {detector_name}",
            )
        axis.set(
            xlim=(-1.1, 12.0), ylim=(-3.0, 3.0), xlabel="x/D", ylabel="y/D",
            title=(f"Cylinder wake, Re={float(cfg['solver']['reynolds_number']):g}, "
                   f"step {row['step']}  |  "
                   f"TP={row['metrics']['true_positive']} "
                   f"FP={row['metrics']['false_positive']} "
                   f"FN={row['metrics']['false_negative']}"),
        )
        axis.set_aspect("equal")
        axis.legend(loc="upper right", framealpha=0.9)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def draw_frequency(path: Path, metrics: dict, cfg: dict) -> None:
    import matplotlib.pyplot as plt

    steps = metrics["steps"]
    nondimensional_time = steps * float(cfg["solver"]["inlet_lattice_velocity"]) / float(
        cfg["solver"]["diameter_cells"]
    )
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 4.3), constrained_layout=True)
    axes[0].plot(nondimensional_time, metrics["signal"], color="#1f5aa6", linewidth=1.2)
    axes[0].set(xlabel=r"$tU_\infty/D$", ylabel=r"wake probe $v'$", title="Periodic shedding signal")
    keep = (metrics["strouhal_axis"] >= 0.05) & (metrics["strouhal_axis"] <= 0.35)
    axes[1].plot(metrics["strouhal_axis"][keep], metrics["power"][keep], color="#a51c30")
    axes[1].axvline(metrics["spectral_strouhal"], color="black", linestyle="--", linewidth=1.2)
    axes[1].set(
        xlabel="Strouhal number", ylabel="spectral power",
        title=f"Dominant St={metrics['spectral_strouhal']:.4f}",
    )
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--sra-config", type=Path)
    parser.add_argument("--temporal-config", type=Path)
    parser.add_argument("--analytic-config", type=Path)
    parser.add_argument("--simulation-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skip-simulation", action="store_true")
    args = parser.parse_args()

    cfg = json.loads((args.config or ROOT / "vortex_cylinder_wake_validation.json").read_text())
    sra_cfg = json.loads((args.sra_config or ROOT / "vortex_shock_ridge_aware_cmcd.json").read_text())
    temporal_cfg = json.loads(args.temporal_config.read_text()) if args.temporal_config else None
    analytic_cfg = json.loads((args.analytic_config or ROOT / "vortex_analytic_positive_control.json").read_text())
    if cfg["frozen_detector_sources"].get("detector_recalibration_allowed") is not False:
        parser.error("cylinder validation must use a frozen detector")

    output = args.output_dir.resolve()
    simulation = args.simulation_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    simulation.mkdir(parents=True, exist_ok=True)
    source = SCRIPT_DIR / "cylinder_lbm_d2q9.c"
    binary = simulation / "cylinder_lbm_d2q9"
    if not args.skip_simulation:
        compile_solver(source, binary)
        run_solver(binary, simulation, cfg, output / "cylinder_solver.log")

    snapshot_paths = sorted(simulation.glob("snapshot_*.bin"))
    expected = 1 + (
        int(cfg["solver"]["total_steps"]) - int(cfg["solver"]["sample_start_step"])
    ) // int(cfg["solver"]["snapshot_stride"])
    if len(snapshot_paths) != expected:
        parser.error(f"incomplete cylinder sequence: {len(snapshot_paths)} != {expected}")
    monitor_path = simulation / "cylinder_monitor.csv"
    if not monitor_path.is_file():
        parser.error("cylinder monitor is missing")

    analytic = load_sibling("cylinder_analytic_tools", "run_vortex_analytic_positive_control.py")
    temporal = (
        load_sibling("cylinder_temporal_tools", "temporal_vortex_recovery.py")
        if temporal_cfg else None
    )
    modules = {
        "base": load_sibling("cylinder_base", "run_vortex_acb_cmcd.py"),
        "artifact": load_sibling("cylinder_artifact", "run_vortex_artifact_aware_acb.py"),
        "sra": load_sibling("cylinder_sra", "run_vortex_shock_ridge_aware_su2.py"),
    }
    x_full, y_full, fluid_full = cylinder_coordinates(cfg)
    xmin, xmax = map(float, cfg["evaluation"]["analysis_x_over_d"])
    ymin, ymax = map(float, cfg["evaluation"]["analysis_y_over_d"])
    xi = np.flatnonzero((x_full >= xmin) & (x_full <= xmax))
    yi = np.flatnonzero((y_full >= ymin) & (y_full <= ymax))
    x, y = x_full[xi], y_full[yi]
    fluid = fluid_full[np.ix_(xi, yi)]
    records: list[dict] = []
    visual_indices = set(np.linspace(
        0, len(snapshot_paths) - 1,
        int(cfg["evaluation"]["physical_figure_count"]), dtype=int,
    ).tolist())
    match_radius = float(cfg["evaluation"]["ground_truth_match_radius_over_d"])
    wall_radius = float(cfg["evaluation"]["near_wall_radius_over_d"])

    for frame, path in enumerate(snapshot_paths):
        step, rho_full, u_full, v_full = read_snapshot(path)
        rho = rho_full[np.ix_(xi, yi)]
        u = u_full[np.ix_(xi, yi)]
        v = v_full[np.ix_(xi, yi)]
        snapshot = {
            "case_id": cfg["case_id"], "category": "cylinder_wake", "x": x, "y": y,
            "u": u, "v": v, "rho": rho, "pressure": rho / 3.0, "fluid": fluid,
            "truth": [], "metadata": {"frame": frame, "step": step},
        }
        omega = vorticity(x, y, u, v)
        gamma2 = gamma2_field(
            u, v, fluid, int(cfg["evaluation"]["gamma2_radius_cells"])
        )
        reference = reference_centers(gamma2, omega, x, y, fluid, cfg)
        detections, runtime = analytic.detect(snapshot, analytic_cfg, sra_cfg, modules)
        records.append({
            "frame_index": frame,
            "step": step,
            "reference": reference,
            "base_detections": detections,
            "detections": list(detections),
            "runtime": runtime,
            "visual": {
                "step": step, "x": x, "y": y, "fluid": fluid, "omega": omega,
                "gamma2": gamma2,
            } if frame in visual_indices else None,
        })

    temporal_audit: list[dict] = []
    if temporal_cfg:
        temporal_audit = temporal.recover(records, temporal_cfg, cfg)

    detector_rows: list[dict] = []
    reference_rows: list[dict] = []
    per_frame: list[dict] = []
    visual_rows: list[dict] = []
    totals = {
        "reference_count": 0, "detection_count": 0, "true_positive": 0,
        "false_positive": 0, "false_negative": 0, "correct_rotation_sign": 0,
        "localization_squared_error": 0.0,
    }
    near_wall_false_positives = 0
    for record in records:
        frame = int(record["frame_index"])
        step = int(record["step"])
        reference = record["reference"]
        detections = record["detections"]
        metrics = match_frame(reference, detections, match_radius)
        for key in totals:
            totals[key] += metrics[key]
        near_wall_false_positives += sum(
            math.hypot(float(row["x"]), float(row["y"])) < wall_radius
            for row in detections
        )
        for rank, row in enumerate(reference, start=1):
            reference_rows.append({"frame_index": frame, "source_step": step, "rank": rank, **row})
        for rank, row in enumerate(detections, start=1):
            detector_rows.append({
                "frame_index": frame, "source_step": step, "rank": rank,
                "x": row["x"], "y": row["y"], "rotation_sign": row["sign"],
                "q_score": row["score"], "shock_ridge_distance_cells": row["shock_ridge_distance_cells"],
                "temporally_recovered": bool(row.get("temporally_recovered", False)),
            })
        recovered_in_frame = sum(
            bool(row.get("temporally_recovered", False)) for row in detections
        )
        per_frame.append({
            "frame_index": frame, "source_step": step,
            **record["runtime"]["diagnostics"],
            "temporally_recovered": recovered_in_frame,
            **metrics,
        })
        if record["visual"] is not None:
            visual_rows.append({
                **record["visual"], "reference": reference,
                "detections": detections, "metrics": metrics,
            })

    frequency = strouhal_metrics(monitor_path, cfg)
    precision = totals["true_positive"] / max(totals["detection_count"], 1)
    recall = totals["true_positive"] / max(totals["reference_count"], 1)
    sign_accuracy = totals["correct_rotation_sign"] / max(totals["true_positive"], 1)
    localization_rmse = math.sqrt(
        totals["localization_squared_error"] / max(totals["true_positive"], 1)
    )
    metrics = {
        **totals,
        "evaluated_frames": len(snapshot_paths),
        "precision": precision,
        "recall": recall,
        "f1": 2.0 * precision * recall / max(precision + recall, 1.0e-300),
        "rotation_sign_accuracy": sign_accuracy,
        "localization_rmse_over_d": localization_rmse,
        "near_wall_false_positives": near_wall_false_positives,
    }
    gates_cfg = cfg["acceptance_gates"]
    strouhal_low, strouhal_high = map(float, gates_cfg["strouhal_number_range"])
    gates = {
        "solver_completed": "pass",
        "frozen_detector": "pass",
        "frozen_temporal_configuration": (
            "pass" if temporal_cfg and temporal_cfg.get("future_case_recalibration_allowed") is False
            else ("not_applicable" if not temporal_cfg else "fail")
        ),
        "time_resolved_sequence": "pass" if len(snapshot_paths) >= int(gates_cfg["minimum_evaluated_frames"]) else "fail",
        "reference_population": "pass" if totals["reference_count"] >= int(gates_cfg["minimum_reference_vortices"]) else "fail",
        "density_stability": "pass" if frequency["maximum_density_deviation"] <= float(gates_cfg["maximum_density_deviation"]) else "fail",
        "von_karman_frequency": "pass" if strouhal_low <= frequency["spectral_strouhal"] <= strouhal_high else "fail",
        "detection_precision": "pass" if precision >= float(gates_cfg["minimum_precision"]) else "fail",
        "detection_recall": "pass" if recall >= float(gates_cfg["minimum_recall"]) else "fail",
        "rotation_sign_accuracy": "pass" if sign_accuracy >= float(gates_cfg["minimum_rotation_sign_accuracy"]) else "fail",
        "near_wall_false_positives": "pass" if near_wall_false_positives <= int(gates_cfg["maximum_near_wall_false_positives"]) else "fail",
    }
    scientific_pass = all(
        value in {"pass", "not_applicable"} for value in gates.values()
    )

    method_cfg = temporal_cfg or sra_cfg
    detector_slug = "tsa_sra_cmcd" if temporal_cfg else "sra_cmcd"
    draw_physical(
        output / f"cylinder_wake_{detector_slug}_physical.png",
        visual_rows,
        cfg,
        str(method_cfg.get("short_name", "SRA-CMCD")),
    )
    draw_frequency(output / "cylinder_wake_frequency_physical.png", frequency, cfg)
    write_csv(output / "cylinder_wake_reference_gamma2.csv", reference_rows, list(reference_rows[0]))
    write_csv(output / f"cylinder_wake_{detector_slug}_detections.csv", detector_rows, list(detector_rows[0]) if detector_rows else ["frame_index", "source_step", "rank", "x", "y", "rotation_sign", "q_score", "shock_ridge_distance_cells", "temporally_recovered"])
    write_csv(output / "cylinder_wake_per_frame.csv", per_frame, list(per_frame[0]))
    if temporal_audit:
        write_csv(
            output / "cylinder_wake_temporal_recovery_audit.csv",
            temporal_audit,
            list(temporal_audit[0]),
        )
    (output / "cylinder_monitor.csv").write_bytes(monitor_path.read_bytes())
    serial_frequency = {
        key: value for key, value in frequency.items()
        if key not in {"steps", "signal", "frequency", "power", "strouhal_axis"}
    }
    role = str(cfg.get("case_role", "")).lower()
    validation_role = str(cfg.get("validation_role", "")).lower()
    independent_holdout = (
        validation_role == "independent_holdout"
        if validation_role else ("independent" in role and "holdout" in role)
    )
    if independent_holdout:
        prefix = "independent_temporal_cylinder_wake_validation" if temporal_cfg else "independent_cylinder_wake_validation"
        claim_gate = f"{prefix}_{'pass' if scientific_pass else 'failed'}"
    else:
        prefix = "temporal_cylinder_wake_development_gate" if temporal_cfg else "cylinder_wake_development_gate"
        claim_gate = f"{prefix}_{'pass' if scientific_pass else 'failed'}"
    report = {
        "schema_version": 1,
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_name": f"{method_cfg['method_name']} cylinder-wake audit",
        "case_id": cfg["case_id"],
        "protocol": cfg,
        "frequency_metrics": serial_frequency,
        "detection_metrics": metrics,
        "temporal_configuration": temporal_cfg,
        "temporally_recovered_detections": sum(
            bool(row["temporally_recovered"]) for row in temporal_audit
        ),
        "gates": gates,
        "claim_gate": claim_gate,
        "limitations": [
            "The D2Q9 BGK solver is a two-dimensional canonical validation, not a turbulent three-dimensional wake.",
            "Gamma_2 is an independent kinematic core reference, not manual ground truth.",
            "A publication claim still requires a second frozen cross-geometry CFD validation.",
        ],
    }
    (output / "cylinder_wake_validation_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("CYLINDER_WAKE_STATUS=completed")
    print(f"CYLINDER_WAKE_STROUHAL={frequency['spectral_strouhal']:.9f}")
    print(f"CYLINDER_WAKE_PRECISION={precision:.9f}")
    print(f"CYLINDER_WAKE_RECALL={recall:.9f}")
    print(f"CYLINDER_WAKE_SIGN_ACCURACY={sign_accuracy:.9f}")
    print(f"CYLINDER_WAKE_TEMPORALLY_RECOVERED={report['temporally_recovered_detections']}")
    print(f"CYLINDER_WAKE_CLAIM_GATE={report['claim_gate']}")
    print(f"CYLINDER_WAKE_GATES={json.dumps(gates, sort_keys=True)}")
    print(f"CYLINDER_WAKE_REPORT={output / 'cylinder_wake_validation_report.json'}")
    return 0 if scientific_pass else 5


if __name__ == "__main__":
    raise SystemExit(main())
