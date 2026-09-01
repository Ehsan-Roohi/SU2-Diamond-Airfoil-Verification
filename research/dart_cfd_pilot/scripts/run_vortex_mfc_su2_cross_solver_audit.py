#!/usr/bin/env python3
"""Apply the frozen TSA-SRA-CMCD-v2 detector to MFC or SU2 airfoil fields.

The alpha=40 MFC and SU2 cases are retrospective diagnostics because both
were inspected during detector development.  This runner never changes a
detector threshold and never upgrades either case to an independent holdout.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import math
import re
import sys
import zipfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import numpy as np
from scipy import ndimage


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


def load_mfc_reference_catalogue(path: Path) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            frame = int(row["frame_index"])
            grouped.setdefault(frame, []).append({
                "x": float(row["x_physical"]), "y": float(row["y_physical"]),
                "sign": int(row["rotation_sign"]),
                "reference_id": row.get("reference_id", ""),
                "gamma2": float(row.get("gamma2") or "nan"),
                "component_cells": 0,
            })
    if not grouped:
        raise RuntimeError(f"MFC reference catalogue is empty: {path}")
    return grouped


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def expected_steps(cfg: dict) -> list[int]:
    return list(range(
        int(cfg["step_start"]), int(cfg["step_stop"]) + 1, int(cfg["step_stride"])
    ))


def field_by_alias(variables: dict, aliases: tuple[str, ...]) -> np.ndarray:
    for name in aliases:
        if name in variables:
            return variables[name]
    raise RuntimeError(
        f"required primitive field {aliases} not found; available={sorted(variables)}"
    )


def reference_centers(
    snapshot: dict, gamma2_radius: int, reference_cfg: dict, bounds: tuple[list, list]
) -> tuple[np.ndarray, list[dict]]:
    stage8 = load_sibling("cross_solver_stage8_reference", "run_dart_stage8_physics_catalogue.py")
    gamma2 = stage8.graftieaux_gamma2(
        snapshot["x"], snapshot["y"], snapshot["u"], snapshot["v"], gamma2_radius
    )
    omega = snapshot["omega"]
    xlim, ylim = bounds
    xx, yy = np.meshgrid(snapshot["x"], snapshot["y"], indexing="ij")
    region = (
        snapshot["fluid"]
        & (xx >= float(xlim[0])) & (xx <= float(xlim[1]))
        & (yy >= float(ylim[0])) & (yy <= float(ylim[1]))
    )
    margin = int(reference_cfg["boundary_margin_cells"])
    safe = np.zeros_like(region)
    if region.shape[0] > 2 * margin and region.shape[1] > 2 * margin:
        safe[margin:-margin, margin:-margin] = True
    region &= safe
    threshold = float(reference_cfg["absolute_gamma2_threshold"])
    minimum = int(reference_cfg["minimum_component_cells"])
    rows: list[dict] = []
    structure = np.ones((3, 3), dtype=np.uint8)
    for polarity in (-1, 1):
        labels, count = ndimage.label(
            region & np.isfinite(gamma2) & (polarity * gamma2 >= threshold),
            structure=structure,
        )
        for component in range(1, count + 1):
            indices = np.flatnonzero(labels == component)
            if indices.size < minimum:
                continue
            flat = int(indices[int(np.argmax(np.abs(gamma2.ravel()[indices])))])
            i, j = np.unravel_index(flat, gamma2.shape)
            rows.append({
                "x": float(snapshot["x"][i]), "y": float(snapshot["y"][j]),
                "sign": 1 if float(omega[i, j]) >= 0.0 else -1,
                "gamma2": float(gamma2[i, j]), "component_cells": int(indices.size),
            })
    return gamma2, rows


def score_frame(reference: list[dict], detections: list[dict], radius: float) -> dict:
    pairs = sorted(
        (
            math.hypot(float(t["x"]) - float(d["x"]), float(t["y"]) - float(d["y"])),
            ti, di,
        )
        for ti, t in enumerate(reference)
        for di, d in enumerate(detections)
    )
    used_truth: set[int] = set()
    used_detection: set[int] = set()
    squared = 0.0
    correct_sign = 0
    for distance, ti, di in pairs:
        if distance > radius or ti in used_truth or di in used_detection:
            continue
        used_truth.add(ti)
        used_detection.add(di)
        squared += distance * distance
        correct_sign += int(int(reference[ti]["sign"]) == int(detections[di]["sign"]))
    return {
        "reference_count": len(reference), "detection_count": len(detections),
        "true_positive": len(used_truth),
        "false_positive": len(detections) - len(used_detection),
        "false_negative": len(reference) - len(used_truth),
        "correct_rotation_sign": correct_sign,
        "localization_squared_error": squared,
        "unmatched_detection_indices": sorted(set(range(len(detections))) - used_detection),
    }


def strict_roi(rows: list[dict], xlim: list, ylim: list) -> list[dict]:
    return [
        row for row in rows
        if float(xlim[0]) < float(row["x"]) < float(xlim[1])
        and float(ylim[0]) < float(row["y"]) < float(ylim[1])
    ]


def attach_geometry_clearance(runtime: dict, clearance: float) -> None:
    snapshot = runtime["snapshot"]
    wall = snapshot["wall_distance"]
    for candidate in runtime["audit"]:
        i, j = int(candidate["grid_i"]), int(candidate["grid_j"])
        distance = float(wall[i, j])
        candidate["wall_distance_over_d"] = distance
        candidate["outside_wall"] = distance >= clearance


def run_detector(snapshot: dict, analytic_cfg: dict, spatial_cfg: dict, modules: dict):
    detections, runtime = modules["analytic"].detect(
        snapshot, analytic_cfg, spatial_cfg, modules
    )
    return detections, runtime


def clean_solid_values(snapshot: dict) -> dict:
    fluid = snapshot["fluid"]
    for name in ("rho", "pressure"):
        values = np.asarray(snapshot[name], dtype=float).copy()
        median = float(np.nanmedian(values[fluid]))
        values[~fluid] = median
        snapshot[name] = values
    for name in ("u", "v"):
        values = np.asarray(snapshot[name], dtype=float).copy()
        values[~fluid] = 0.0
        snapshot[name] = values
    return snapshot


def load_mfc_snapshots(
    case_dir: Path, mfc_root: Path, cfg: dict, geometry, visual_indices: set[int]
):
    marker = case_dir / str(cfg["required_marker"])
    if not marker.is_file() or "status=PASS" not in marker.read_text().splitlines():
        raise RuntimeError(f"completed MFC raw-field marker is invalid: {marker}")
    sys.path.insert(0, str(mfc_root / "toolchain"))
    from mfc.viz.reader import assemble, discover_timesteps

    steps = expected_steps(cfg)
    available = set(discover_timesteps(str(case_dir), "binary"))
    missing = sorted(set(steps) - available)
    if missing:
        raise RuntimeError(f"MFC sequence incomplete: {len(missing)} missing; first={missing[0]}")
    for frame, step in enumerate(steps):
        assembled = assemble(str(case_dir), step, fmt="binary")
        xi = np.flatnonzero(
            (assembled.x_cc >= float(cfg["analysis_xlim"][0]))
            & (assembled.x_cc <= float(cfg["analysis_xlim"][1]))
        )
        yi = np.flatnonzero(
            (assembled.y_cc >= float(cfg["analysis_ylim"][0]))
            & (assembled.y_cc <= float(cfg["analysis_ylim"][1]))
        )
        if not xi.size or not yi.size:
            raise RuntimeError("MFC analysis crop does not overlap the raw grid")
        xi = np.arange(max(0, xi[0] - 3), min(assembled.x_cc.size, xi[-1] + 4))
        yi = np.arange(max(0, yi[0] - 3), min(assembled.y_cc.size, yi[-1] + 4))
        x, y = assembled.x_cc[xi].copy(), assembled.y_cc[yi].copy()
        variables = assembled.variables
        snapshot = {
            "case_id": cfg["case_id"], "category": "compressible_mfc_airfoil",
            "x": x, "y": y,
            "u": field_by_alias(variables, ("vel1",))[np.ix_(xi, yi)].copy(),
            "v": field_by_alias(variables, ("vel2",))[np.ix_(xi, yi)].copy(),
            "rho": field_by_alias(variables, ("rho", "density"))[np.ix_(xi, yi)].copy(),
            "pressure": field_by_alias(variables, ("pres", "pressure"))[np.ix_(xi, yi)].copy(),
            "fluid": geometry.geometry_fluid_mask(x, y),
            "truth": [], "metadata": {"frame": frame, "step": step},
        }
        finite = np.logical_and.reduce([
            np.isfinite(snapshot[name]) for name in ("u", "v", "rho", "pressure")
        ])
        if not np.all(finite[snapshot["fluid"]]):
            raise RuntimeError(f"non-finite MFC primitive field at step {step}")
        # Yield one state at a time: the production crop is large enough that
        # retaining all 61 primitive-field arrays would exceed the job's
        # declared memory even though the detector itself is frame-local.
        yield {"frame": frame, "step": step, "snapshot": clean_solid_values(snapshot),
               "visual": frame in visual_indices}


def load_su2_snapshots(checkpoint: Path, cfg: dict, geometry) -> list[dict]:
    sra = load_sibling("cross_solver_su2_reader", "run_vortex_shock_ridge_aware_su2.py")
    spacing = float(cfg["raster_spacing"])
    x = np.arange(float(cfg["analysis_xlim"][0]), float(cfg["analysis_xlim"][1]) + 0.5 * spacing, spacing)
    y = np.arange(float(cfg["analysis_ylim"][0]), float(cfg["analysis_ylim"][1]) + 0.5 * spacing, spacing)
    geometry_fluid = geometry.geometry_fluid_mask(x, y)
    rows: list[dict] = []
    with zipfile.ZipFile(checkpoint) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f"corrupt SU2 checkpoint member: {bad}")
        members = list(cfg["restart_members"])
        missing = [name for name in members if name not in archive.namelist()]
        if missing:
            raise RuntimeError(f"SU2 checkpoint lacks restart members: {missing}")
        triangulation = None
        coordinates0 = None
        for frame, member in enumerate(members):
            raw = sra.read_su2_restart(archive, member, float(cfg["gamma"]))
            coordinates = np.column_stack((raw["x"], raw["y"]))
            if triangulation is None:
                from scipy.spatial import Delaunay
                triangulation = Delaunay(coordinates)
                coordinates0 = coordinates
            elif not np.array_equal(coordinates, coordinates0):
                raise RuntimeError("SU2 O-grid coordinates change between snapshots")
            native = sra.derive_native_ogrid_fields(
                raw, int(cfg["radial_points"]), int(cfg["circumferential_points"])
            )
            fields = sra.interpolate_native_fields(triangulation, native, x, y)
            finite = np.logical_and.reduce([
                np.isfinite(fields[name]) for name in ("u", "v", "rho", "pressure")
            ])
            fluid = geometry_fluid & finite
            snapshot = clean_solid_values({
                "case_id": cfg["case_id"], "category": "compressible_su2_airfoil",
                "x": x, "y": y, "u": fields["u"], "v": fields["v"],
                "rho": fields["rho"], "pressure": fields["pressure"],
                "fluid": fluid, "truth": [],
                "metadata": {"frame": frame, "member": member},
            })
            match = re.search(r"_(\d+)\.csv$", member)
            rows.append({"frame": frame, "step": int(match.group(1)) if match else frame,
                         "snapshot": snapshot, "visual": True, "member": member})
    return rows


def draw_physical(path: Path, visuals: list[dict], cfg: dict, solver: str) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(len(visuals), 1, figsize=(14.5, 4.4 * len(visuals)), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for axis, row in zip(axes, visuals):
        field = np.where(row["fluid"], row["omega"], np.nan)
        limit = max(float(np.nanpercentile(np.abs(field), 99.4)), 1.0e-12)
        axis.contourf(row["x"], row["y"], field.T, levels=np.linspace(-limit, limit, 101),
                      cmap="RdBu_r", extend="both")
        shock = row.get("shock_ridge_mask")
        if shock is not None and np.any(shock):
            axis.contour(row["x"], row["y"], shock.T.astype(float), levels=[0.5],
                         colors="#b000d0", linewidths=0.8)
        reference = row["reference"]
        raw_gamma2 = row.get("raw_gamma2_reference", [])
        detections = row["detections"]
        if raw_gamma2:
            axis.scatter([r["x"] for r in raw_gamma2], [r["y"] for r in raw_gamma2],
                         marker="+", s=32, c="0.35", linewidths=0.8,
                         label=r"raw $\Gamma_2$ artifacts (unqualified)")
        if reference:
            axis.scatter([r["x"] for r in reference], [r["y"] for r in reference],
                         marker="+", s=72, c="black", linewidths=1.4, label=r"$\Gamma_2$ reference")
        if detections:
            colors = ["#0055cc" if int(r["sign"]) < 0 else "#d62728" for r in detections]
            axis.scatter([r["x"] for r in detections], [r["y"] for r in detections],
                         s=62, facecolors="none", edgecolors=colors, linewidths=1.5,
                         label="TSA-SRA-CMCD-v2")
        axis.set(xlim=cfg["figure_xlim"], ylim=cfg["figure_ylim"], xlabel="x/c", ylabel="y/c")
        axis.set_aspect("equal")
        axis.set_title(
            f"{solver.upper()} frame {row['frame']} | TP={row['metrics']['true_positive']} "
            f"FP={row['metrics']['false_positive']} FN={row['metrics']['false_negative']}"
        )
        handles, labels = axis.get_legend_handles_labels()
        if handles:
            axis.legend(loc="upper right", fontsize=8)
    fig.suptitle("Frozen cross-solver vortex-core audit", fontsize=15)
    fig.savefig(path, dpi=230, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", choices=("mfc", "su2"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--mfc-root", type=Path)
    parser.add_argument("--reference-catalogue", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    config = json.loads((args.config or ROOT / "vortex_mfc_su2_cross_solver_audit.json").read_text())
    if config.get("future_case_recalibration_allowed") is not False:
        parser.error("cross-solver audit configuration is not frozen")
    analytic_cfg = json.loads((ROOT / config["frozen_sources"]["candidate_protocol"]).read_text())
    spatial_cfg = json.loads((ROOT / config["frozen_sources"]["spatial_configuration"]).read_text())
    temporal_cfg = json.loads((ROOT / config["frozen_sources"]["temporal_configuration"]).read_text())
    if any(item.get("future_case_recalibration_allowed") is not False
           for item in (analytic_cfg, spatial_cfg, temporal_cfg)):
        parser.error("one or more detector components are not frozen")
    modules = {
        "analytic": load_sibling("cross_solver_analytic", "run_vortex_analytic_positive_control.py"),
        "base": load_sibling("cross_solver_base", "run_vortex_acb_cmcd.py"),
        "artifact": load_sibling("cross_solver_artifact", "run_vortex_artifact_aware_acb.py"),
        "sra": load_sibling("cross_solver_sra", "run_vortex_shock_ridge_aware_su2.py"),
    }
    geometry = load_sibling("cross_solver_geometry", "run_dart_stage8_physics_catalogue.py")
    temporal = load_sibling("cross_solver_temporal", "temporal_vortex_recovery.py")
    case_cfg = config[args.solver]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    if args.solver == "mfc":
        if args.mfc_root is None:
            parser.error("--mfc-root is required for MFC")
        if args.reference_catalogue is None or not args.reference_catalogue.is_file():
            parser.error("--reference-catalogue is required for MFC")
        mfc_reference = load_mfc_reference_catalogue(args.reference_catalogue.resolve())
        count = len(expected_steps(case_cfg))
        visual_indices = set(np.linspace(0, count - 1, 3, dtype=int).tolist())
        sources = load_mfc_snapshots(args.input.resolve(), args.mfc_root.resolve(), case_cfg,
                                     geometry, visual_indices)
        input_provenance = {
            "case_dir": str(args.input.resolve()), "mfc_root": str(args.mfc_root.resolve()),
            "reference_catalogue": str(args.reference_catalogue.resolve()),
            "reference_catalogue_sha256": file_sha256(args.reference_catalogue.resolve()),
        }
    else:
        mfc_reference = {}
        sources = load_su2_snapshots(args.input.resolve(), case_cfg, geometry)
        input_provenance = {"checkpoint": str(args.input.resolve()), "sha256": file_sha256(args.input.resolve())}

    records: list[dict] = []
    clearance = float(case_cfg["equivalent_cylinder_wall_clearance_over_c"])
    for source in sources:
        snapshot = source["snapshot"]
        detections, runtime = run_detector(snapshot, analytic_cfg, spatial_cfg, modules)
        attach_geometry_clearance(runtime, clearance)
        raw_gamma2_reference: list[dict] = []
        if args.solver == "mfc":
            gamma2 = None
            reference = mfc_reference.get(int(source["frame"]), [])
            if not reference:
                raise RuntimeError(
                    f"MFC reference catalogue lacks frame {int(source['frame'])}"
                )
        else:
            gamma2, raw_gamma2_reference = reference_centers(
                runtime["snapshot"], int(case_cfg["gamma2_radius_cells"]),
                config["reference"], (case_cfg["analysis_xlim"], case_cfg["analysis_ylim"]),
            )
            # SU2 alpha=40 was predeclared as a shock-rich negative control.
            # Raw Gamma2 components are an artifact census, never truth labels.
            reference = []
        records.append({
            "frame_index": int(source["frame"]), "step": int(source["step"]),
            "source_step": int(source["step"]), "reference": reference,
            "raw_gamma2_reference": raw_gamma2_reference,
            "base_detections": list(detections), "detections": list(detections),
            "runtime": {"audit": runtime["audit"], "diagnostics": runtime["diagnostics"]},
            "visual": {
                "frame": int(source["frame"]), "x": snapshot["x"], "y": snapshot["y"],
                "fluid": snapshot["fluid"], "omega": runtime["snapshot"]["omega"],
                "gamma2": gamma2, "shock_ridge_mask": runtime["snapshot"]["shock_ridge_mask"],
            } if source["visual"] else None,
        })

    temporal_audit: list[dict] = []
    temporal_ran = args.solver == "mfc" and len(records) >= int(
        config["acceptance_gates"]["minimum_temporal_frames"]
    )
    if temporal_ran:
        protocol = {"solver": {
            "inlet_lattice_velocity": (
                float(case_cfg["freestream_velocity"]) * float(case_cfg["snapshot_dt"])
                / float(case_cfg["step_stride"])
            ),
            "diameter_cells": 1.0,
        }}
        temporal_audit = temporal.recover(records, temporal_cfg, protocol)

    detector_rows: list[dict] = []
    reference_rows: list[dict] = []
    raw_gamma2_rows: list[dict] = []
    per_frame: list[dict] = []
    visuals: list[dict] = []
    totals = {key: 0 for key in (
        "reference_count", "detection_count", "true_positive", "false_positive",
        "false_negative", "correct_rotation_sign"
    )}
    localization_squared_error = 0.0
    near_body_false_positives = 0
    match_radius = float(config["reference"]["match_radius_over_c"])
    for record in records:
        detections = strict_roi(
            record["detections"], case_cfg["analysis_xlim"], case_cfg["analysis_ylim"]
        )
        metrics = score_frame(record["reference"], detections, match_radius)
        for key in totals:
            totals[key] += int(metrics[key])
        localization_squared_error += float(metrics["localization_squared_error"])
        near_body_false_positives += sum(
            not bool(detections[index].get("outside_wall", False))
            for index in metrics["unmatched_detection_indices"]
        )
        frame = int(record["frame_index"])
        step = int(record["source_step"])
        for rank, row in enumerate(record["reference"], 1):
            reference_rows.append({"frame_index": frame, "source_step": step, "rank": rank, **row})
        for rank, row in enumerate(record["raw_gamma2_reference"], 1):
            raw_gamma2_rows.append({"frame_index": frame, "source_step": step, "rank": rank, **row})
        for rank, row in enumerate(detections, 1):
            detector_rows.append({
                "frame_index": frame, "source_step": step, "rank": rank,
                "x": row["x"], "y": row["y"], "rotation_sign": row["sign"],
                "q_score": row["score"],
                "shock_ridge_distance_cells": row["shock_ridge_distance_cells"],
                "wall_distance_over_c": row.get("wall_distance_over_d"),
                "temporally_recovered": bool(row.get("temporally_recovered", False)),
            })
        per_frame.append({
            "frame_index": frame, "source_step": step,
            **record["runtime"]["diagnostics"],
            **{key: value for key, value in metrics.items() if key != "unmatched_detection_indices"},
            "temporally_recovered": sum(bool(row.get("temporally_recovered", False)) for row in detections),
        })
        if record["visual"] is not None:
            visuals.append({**record["visual"], "reference": record["reference"],
                            "raw_gamma2_reference": record["raw_gamma2_reference"],
                            "detections": detections, "metrics": metrics})

    negative_control = case_cfg.get("evaluation_role") == "development_negative_control"
    precision = (
        None if negative_control
        else totals["true_positive"] / max(totals["detection_count"], 1)
    )
    recall = (
        None if negative_control
        else totals["true_positive"] / max(totals["reference_count"], 1)
    )
    sign_accuracy = (
        None if negative_control
        else totals["correct_rotation_sign"] / max(totals["true_positive"], 1)
    )
    f1 = (
        None if negative_control
        else 2.0 * precision * recall / max(precision + recall, 1.0e-300)
    )
    localization = math.sqrt(localization_squared_error / max(totals["true_positive"], 1))
    metrics = {
        **totals, "evaluated_frames": len(records), "precision": precision,
        "recall": recall, "f1": f1, "rotation_sign_accuracy": sign_accuracy,
        "localization_rmse_over_c": localization,
        "near_body_false_positives": near_body_false_positives,
        "raw_gamma2_artifact_components": len(raw_gamma2_rows),
        "temporally_recovered_detections": sum(
            bool(row["temporally_recovered"]) for row in temporal_audit
        ),
    }
    gate_cfg = config["acceptance_gates"]
    gates = {
        "raw_input_integrity": "pass",
        "frozen_detector": "pass",
        "reference_population": (
            "not_applicable_negative_control" if negative_control
            else ("pass" if totals["reference_count"] >= int(gate_cfg["minimum_reference_vortices"]) else "fail")
        ),
        "detection_precision": (
            "not_applicable_negative_control" if negative_control
            else ("pass" if precision >= float(gate_cfg["minimum_precision"]) else "fail")
        ),
        "detection_recall": (
            "not_applicable_negative_control" if negative_control
            else ("pass" if recall >= float(gate_cfg["minimum_recall"]) else "fail")
        ),
        "rotation_sign_accuracy": (
            "not_applicable_negative_control" if negative_control
            else ("pass" if sign_accuracy >= float(gate_cfg["minimum_rotation_sign_accuracy"]) else "fail")
        ),
        "near_body_false_positives": "pass" if near_body_false_positives <= int(gate_cfg["maximum_near_body_false_positives"]) else "fail",
        "negative_control_false_vortices": (
            "pass" if negative_control and totals["detection_count"] == 0
            else ("fail" if negative_control else "not_applicable")
        ),
        "time_resolved_sequence": "pass" if len(records) >= int(gate_cfg["minimum_temporal_frames"]) else "fail",
        "temporal_detector_exercised": "pass" if temporal_ran else "fail",
        "independent_holdout": "fail"
    }
    quality_keys = (
        ["negative_control_false_vortices", "near_body_false_positives"]
        if negative_control else
        ["reference_population", "detection_precision", "detection_recall",
         "rotation_sign_accuracy", "near_body_false_positives"]
    )
    quality_pass = all(gates[key] == "pass" for key in quality_keys)
    report = {
        "schema_version": 1, "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_name": config["method_name"], "solver": args.solver,
        "case_id": case_cfg["case_id"], "case_role": config["case_role"],
        "input": input_provenance, "frozen_sources": config["frozen_sources"],
        "configuration": case_cfg, "reference_protocol": config["reference"],
        "negative_control": negative_control,
        "detection_metrics": metrics, "per_frame": per_frame,
        "gates": gates,
        "diagnostic_quality_gate": "pass" if quality_pass else "fail",
        "claim_gate": (
            "retrospective_cross_solver_diagnostic_pass_not_independent"
            if quality_pass else "retrospective_cross_solver_diagnostic_failed"
        ),
        "limitations": [
            "The alpha-40 airfoil cases were visible during method development and are not independent holdouts.",
            "Gamma2 is an independent kinematic reference, not human-annotated ground truth.",
            "The SU2 archive contains two adjacent snapshots, so TSA temporal recovery cannot be validated on SU2.",
            "This is two-dimensional vortex-core localization, not three-dimensional vortex-tube segmentation."
        ],
    }
    slug = f"{args.solver}_cross_solver"
    write_csv(output / f"{slug}_reference_gamma2.csv", reference_rows,
              ["frame_index", "source_step", "rank", "x", "y", "sign", "gamma2", "component_cells"])
    if raw_gamma2_rows:
        write_csv(output / f"{slug}_raw_gamma2_artifact_census.csv", raw_gamma2_rows,
                  ["frame_index", "source_step", "rank", "x", "y", "sign", "gamma2", "component_cells"])
    write_csv(output / f"{slug}_tsa_sra_cmcd_v2_detections.csv", detector_rows,
              ["frame_index", "source_step", "rank", "x", "y", "rotation_sign", "q_score",
               "shock_ridge_distance_cells", "wall_distance_over_c", "temporally_recovered"])
    write_csv(output / f"{slug}_per_frame.csv", per_frame,
              sorted({key for row in per_frame for key in row}))
    if temporal_audit:
        write_csv(output / f"{slug}_temporal_recovery_audit.csv", temporal_audit,
                  list(temporal_audit[0]))
    draw_physical(output / f"{slug}_tsa_sra_cmcd_v2_physical.png", visuals, case_cfg, args.solver)
    (output / f"{slug}_report.json").write_text(
        json.dumps(report, indent=2, default=json_default) + "\n"
    )
    print(f"CROSS_SOLVER_STATUS=completed")
    print(f"CROSS_SOLVER_SOLVER={args.solver}")
    print(f"CROSS_SOLVER_PRECISION={'NA' if precision is None else f'{precision:.9f}'}")
    print(f"CROSS_SOLVER_RECALL={'NA' if recall is None else f'{recall:.9f}'}")
    print(f"CROSS_SOLVER_F1={'NA' if f1 is None else f'{f1:.9f}'}")
    print(f"CROSS_SOLVER_SIGN_ACCURACY={'NA' if sign_accuracy is None else f'{sign_accuracy:.9f}'}")
    print(f"CROSS_SOLVER_TEMPORAL_RAN={int(temporal_ran)}")
    print(f"CROSS_SOLVER_CLAIM_GATE={report['claim_gate']}")
    print(f"CROSS_SOLVER_REPORT={output / f'{slug}_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
