#!/usr/bin/env python3
"""Apply TSA-SRA-CMCD-v3 to the existing two-state SU2 alpha=40 checkpoint.

This is development-only retrospective evidence.  The SU2 states were already
inspected while the v3 rule was being defined, so this script may test whether
v3 rescues the known strong shock-overlap miss, but it must never be described
as an independent holdout or as a precision/recall validation.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
KNOWN_TARGET = (0.9574, 0.0944)


def load_sibling(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def distance(a: dict, b: dict | tuple[float, float]) -> float:
    if isinstance(b, tuple):
        bx, by = b
    else:
        bx, by = float(b["x"]), float(b["y"])
    return math.hypot(float(a["x"]) - bx, float(a["y"]) - by)


def unmatched(new_rows: list[dict], old_rows: list[dict], tolerance: float = 1.0e-10) -> list[dict]:
    return [
        row for row in new_rows
        if not any(distance(row, other) <= tolerance for other in old_rows)
    ]


def candidate_summary(row: dict | None) -> dict | None:
    if row is None:
        return None
    pressure = row.get("pressure_core", {})
    return {
        "x": float(row["x"]),
        "y": float(row["y"]),
        "rotation_sign": int(row["sign"]),
        "accepted": bool(row.get("accepted", False)),
        "rejection_reason": row.get("rejection_reason"),
        "q_score": float(row.get("score", float("nan"))),
        "winding_support": int(row.get("winding_support", 0)),
        "q_island_closed": bool(row.get("q_island_closed", False)),
        "q_island_area_cells": int(row.get("q_island_area_cells", 0)),
        "q_island_aspect_ratio": float(row.get("q_island_aspect_ratio", float("nan"))),
        "equivalent_q_radius_cells": float(row.get("equivalent_q_radius_cells", float("nan"))),
        "pressure_ring_support": int(pressure.get("ring_support", 0)),
        "pressure_minimum_offset_cells": float(pressure.get("offset_cells", float("nan"))),
        "pressure_pass": bool(row.get("pressure_pass", pressure.get("pass", False))),
        "shock_ridge_distance_cells": float(row.get("shock_ridge_distance_cells", float("nan"))),
        "rotation_purity": float(row.get("rotation_purity", float("nan"))),
        "sign_coherence": float(row.get("sign_coherence", float("nan"))),
        "ring_coherence": float(row.get("ring_coherence", float("nan"))),
        "radial_to_tangential": float(row.get("radial_to_tangential", float("nan"))),
        "scale_persistence": float(row.get("scale_persistence", float("nan"))),
        "hessian_compactness": float(row.get("hessian_compactness", float("nan"))),
        "wall_distance_over_c": float(row.get("wall_distance_over_d", float("nan"))),
    }


def draw_physical(path: Path, records: list[dict], case_cfg: dict, cross) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.tri as mtri

    fig, axes = plt.subplots(len(records), 2, figsize=(14.5, 5.0 * len(records)), constrained_layout=True)
    axes = np.atleast_2d(axes)

    for row_index, record in enumerate(records):
        source = record["source"]
        native = source["native_visual"]
        coordinates = native["coordinates"]
        triangles = native["triangles"]
        omega = native["omega"]
        surface_distance = cross.diamond_surface_distance(coordinates[:, 0], coordinates[:, 1])
        triangulation = mtri.Triangulation(coordinates[:, 0], coordinates[:, 1], triangles.copy())
        triangulation.set_mask(~np.all(surface_distance[triangles] >= 0.02, axis=1))
        roi = (
            (coordinates[:, 0] >= float(case_cfg["figure_xlim"][0]))
            & (coordinates[:, 0] <= float(case_cfg["figure_xlim"][1]))
            & (coordinates[:, 1] >= float(case_cfg["figure_ylim"][0]))
            & (coordinates[:, 1] <= float(case_cfg["figure_ylim"][1]))
            & (surface_distance >= 0.025)
            & np.isfinite(omega)
        )
        limit = max(float(np.percentile(np.abs(omega[roi]), 99.0)), 1.0e-12)

        target = record["target"]
        target_x = float(target["x"]) if target is not None else KNOWN_TARGET[0]
        target_y = float(target["y"]) if target is not None else KNOWN_TARGET[1]

        for column, axis in enumerate(axes[row_index]):
            axis.tricontourf(
                triangulation, omega, levels=np.linspace(-limit, limit, 101),
                cmap="RdBu_r", extend="both",
            )
            snapshot = record["v3_runtime"]["snapshot"]
            shock = snapshot["shock_ridge_mask"]
            if np.any(shock):
                axis.contour(
                    snapshot["x"], snapshot["y"], shock.T.astype(float),
                    levels=[0.5], colors="#c000ff", linewidths=1.0,
                )

            v2 = record["v2_detections"]
            if v2:
                axis.scatter(
                    [r["x"] for r in v2], [r["y"] for r in v2],
                    s=70, facecolors="none", edgecolors="#00a6d6", linewidths=1.8,
                    label="v2 accepted",
                )
            strong = record["v2_strong_misses"]
            if strong:
                axis.scatter(
                    [r["x"] for r in strong], [r["y"] for r in strong],
                    s=150, facecolors="none", edgecolors="#ffbf00", linewidths=2.5,
                    label="v2 strong-topology miss",
                )
            v3 = record["v3_detections"]
            if v3:
                axis.scatter(
                    [r["x"] for r in v3], [r["y"] for r in v3],
                    s=78, facecolors="none", edgecolors="#00d070", linewidths=2.2,
                    label="v3 accepted",
                )

            axis.set_aspect("equal")
            axis.set_xlabel("x/c")
            axis.set_ylabel("y/c")
            if column == 0:
                axis.set_xlim(*case_cfg["figure_xlim"])
                axis.set_ylim(*case_cfg["figure_ylim"])
                axis.set_title(
                    f"SU2 step {source['step']} | v2={len(v2)} v3={len(v3)} | "
                    f"new={len(record['new_v3'])}"
                )
            else:
                half_width = 0.24
                axis.set_xlim(target_x - half_width, target_x + half_width)
                axis.set_ylim(target_y - half_width, target_y + half_width)
                axis.set_title(
                    f"shock-overlap target | rescued={int(record['target_rescued'])} | "
                    f"non-target new={record['non_target_new_count']}"
                )
            handles, labels = axis.get_legend_handles_labels()
            if handles:
                axis.legend(loc="upper right", fontsize=8)

    fig.suptitle(
        "Retrospective SU2 Mach-3 alpha=40: TSA-SRA-CMCD-v2 vs shock-conditioned v3",
        fontsize=15,
    )
    fig.savefig(path, dpi=240, bbox_inches="tight")
    plt.close(fig)


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = [
        "frame", "source_step", "v2_detections", "v2_strong_misses",
        "v3_detections", "new_v3_detections", "target_rescued",
        "non_target_new_acceptances", "target_x", "target_y",
        "target_v2_reason", "target_v3_reason", "target_v3_shock_distance_cells",
        "target_v3_winding_support", "target_v3_pressure_ring_support",
        "target_v3_equivalent_q_radius_cells", "target_v3_rotation_purity",
    ]
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    cross = load_sibling("su2v3_cross", "run_vortex_mfc_su2_cross_solver_audit.py")
    analytic = load_sibling("su2v3_analytic", "run_vortex_analytic_positive_control.py")
    v3 = load_sibling("su2v3_v3", "run_vortex_analytic_v3_shock_conditioned.py")
    base = load_sibling("su2v3_base", "run_vortex_acb_cmcd.py")
    artifact = load_sibling("su2v3_artifact", "run_vortex_artifact_aware_acb.py")
    sra = load_sibling("su2v3_sra", "run_vortex_shock_ridge_aware_su2.py")
    geometry = load_sibling("su2v3_geometry", "run_dart_stage8_physics_catalogue.py")

    cross_cfg = json.loads((ROOT / "vortex_mfc_su2_cross_solver_audit.json").read_text())
    analytic_cfg = json.loads((ROOT / "vortex_analytic_positive_control.json").read_text())
    spatial_cfg = json.loads((ROOT / "vortex_scale_adaptive_sra_cmcd.json").read_text())
    v3_cfg = json.loads((ROOT / "vortex_shock_conditioned_cmcd_v3_dev.json").read_text())
    case_cfg = cross_cfg["su2"]
    if v3_cfg.get("future_case_recalibration_allowed") is not False:
        parser.error("v3 rule is not frozen")

    checkpoint = args.checkpoint.resolve()
    if not checkpoint.is_file():
        parser.error(f"checkpoint not found: {checkpoint}")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    sources = cross.load_su2_snapshots(checkpoint, case_cfg, geometry)
    modules = {"base": base, "artifact": artifact, "sra": sra}
    clearance = float(case_cfg["equivalent_cylinder_wall_clearance_over_c"])
    records: list[dict] = []
    flat_rows: list[dict] = []

    for source in sources:
        snapshot = source["snapshot"]
        v2_detections, v2_runtime = analytic.detect(snapshot, analytic_cfg, spatial_cfg, modules)
        cross.attach_geometry_clearance(v2_runtime, clearance)
        strong_misses = cross.missed_strong_topology_candidates(
            v2_runtime["audit"], case_cfg["missed_strong_topology_audit"]
        )

        v3_detections, v3_runtime = v3.detect_v3(
            snapshot, analytic_cfg, spatial_cfg, v3_cfg, modules
        )
        cross.attach_geometry_clearance(v3_runtime, clearance)
        new_v3 = unmatched(v3_detections, v2_detections)

        target = (
            min(strong_misses, key=lambda r: distance(r, KNOWN_TARGET))
            if strong_misses else
            min(v2_runtime["audit"], key=lambda r: distance(r, KNOWN_TARGET))
        )
        target_rescued = any(distance(row, target) <= 0.03 for row in v3_detections)
        non_target_new = [row for row in new_v3 if distance(row, target) > 0.03]
        v3_target = min(v3_runtime["audit"], key=lambda r: distance(r, target))

        record = {
            "source": source,
            "v2_detections": list(v2_detections),
            "v2_runtime": v2_runtime,
            "v2_strong_misses": list(strong_misses),
            "v3_detections": list(v3_detections),
            "v3_runtime": v3_runtime,
            "new_v3": list(new_v3),
            "target": target,
            "target_rescued": bool(target_rescued),
            "non_target_new_count": len(non_target_new),
            "v3_target": v3_target,
        }
        records.append(record)
        pressure = v3_target.get("pressure_core", {})
        flat_rows.append({
            "frame": int(source["frame"]),
            "source_step": int(source["step"]),
            "v2_detections": len(v2_detections),
            "v2_strong_misses": len(strong_misses),
            "v3_detections": len(v3_detections),
            "new_v3_detections": len(new_v3),
            "target_rescued": int(target_rescued),
            "non_target_new_acceptances": len(non_target_new),
            "target_x": float(target["x"]),
            "target_y": float(target["y"]),
            "target_v2_reason": target.get("rejection_reason"),
            "target_v3_reason": v3_target.get("rejection_reason"),
            "target_v3_shock_distance_cells": float(v3_target.get("shock_ridge_distance_cells", float("nan"))),
            "target_v3_winding_support": int(v3_target.get("winding_support", 0)),
            "target_v3_pressure_ring_support": int(pressure.get("ring_support", 0)),
            "target_v3_equivalent_q_radius_cells": float(v3_target.get("equivalent_q_radius_cells", float("nan"))),
            "target_v3_rotation_purity": float(v3_target.get("rotation_purity", float("nan"))),
        })

    gates = {
        "known_strong_miss_present_each_snapshot": "pass" if all(r["v2_strong_misses"] for r in records) else "fail",
        "known_target_rescued_each_snapshot": "pass" if all(r["target_rescued"] for r in records) else "fail",
        "no_non_target_new_acceptances": "pass" if sum(r["non_target_new_count"] for r in records) == 0 else "fail",
        "two_adjacent_states_reproduced": "pass" if len(records) == 2 else "fail",
    }
    claim_gate = (
        "retrospective_su2_shock_conditioned_target_rescue_pass"
        if all(value == "pass" for value in gates.values())
        else "retrospective_su2_shock_conditioned_target_rescue_failed"
    )

    draw_physical(output / "su2_v3_retro_physical.png", records, case_cfg, cross)
    write_csv(output / "su2_v3_retro_per_frame.csv", flat_rows)

    report_records = []
    for record in records:
        report_records.append({
            "frame": int(record["source"]["frame"]),
            "source_step": int(record["source"]["step"]),
            "source_member": record["source"].get("member"),
            "v2_detection_count": len(record["v2_detections"]),
            "v2_strong_miss_count": len(record["v2_strong_misses"]),
            "v3_detection_count": len(record["v3_detections"]),
            "new_v3_detection_count": len(record["new_v3"]),
            "target_rescued": bool(record["target_rescued"]),
            "non_target_new_acceptances": int(record["non_target_new_count"]),
            "v2_target": candidate_summary(record["target"]),
            "v3_target": candidate_summary(record["v3_target"]),
            "v3_detections": [candidate_summary(row) for row in record["v3_detections"]],
        })

    report = {
        "schema_version": 1,
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_name": v3_cfg["method_name"],
        "case_id": case_cfg["case_id"],
        "evidence_role": "retrospective development-only SU2 shock-overlap rescue diagnostic",
        "ground_truth_status": case_cfg["ground_truth_status"],
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": cross.file_sha256(checkpoint),
        "known_target_nominal_xy": list(KNOWN_TARGET),
        "per_snapshot": report_records,
        "gates": gates,
        "claim_gate": claim_gate,
        "limitations": [
            "The SU2 alpha=40 states were inspected during method development and are not an independent holdout.",
            "The candidate near (0.9574,0.0944) is a strong rotational-topology candidate, not human-adjudicated ground truth.",
            "Only two adjacent SU2 states are available, so temporal tracking cannot be validated here.",
            "A successful rescue only licenses freezing v3 before a new unseen time-resolved compressible case."
        ],
    }
    (output / "su2_v3_retro_report.json").write_text(
        json.dumps(report, indent=2, default=cross.json_default) + "\n"
    )

    print("SU2_V3_RETRO_STATUS=completed")
    print(f"SU2_V3_RETRO_CLAIM_GATE={claim_gate}")
    for row in flat_rows:
        print(
            "SU2_V3_FRAME="
            f"{row['frame']} STEP={row['source_step']} "
            f"V2={row['v2_detections']} V3={row['v3_detections']} "
            f"TARGET_RESCUED={row['target_rescued']} "
            f"NON_TARGET_NEW={row['non_target_new_acceptances']} "
            f"TARGET_REASON={row['target_v3_reason']}"
        )
    return 0 if claim_gate.endswith("_pass") else 5


if __name__ == "__main__":
    raise SystemExit(main())
