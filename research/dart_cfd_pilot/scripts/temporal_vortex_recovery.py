#!/usr/bin/env python3
"""Physics-gated temporal recovery for conservative vortex-core detections."""
from __future__ import annotations

import math


def physics_support(candidate: dict, cfg: dict) -> tuple[int, dict]:
    checks = {
        "closed_q_island": bool(candidate["q_island_pass"]),
        "multiradius_winding": int(candidate["winding_support"])
        >= int(cfg["minimum_winding_ring_support"]),
        "pressure_ring_corroboration": int(candidate["pressure_core"]["ring_support"])
        >= int(cfg["minimum_pressure_ring_support"]),
    }
    return sum(checks.values()), checks


def temporal_supports(
    candidate: dict,
    frame_index: int,
    records: list[dict],
    cfg: dict,
    protocol: dict,
) -> list[dict]:
    solver = protocol["solver"]
    inlet_velocity = float(solver["inlet_lattice_velocity"])
    diameter = float(solver["diameter_cells"])
    lookaround = int(cfg["lookaround_frames"])
    maximum_speed = float(cfg["maximum_convection_speed_over_u_infinity"])
    upstream_tolerance = float(cfg["maximum_upstream_backtracking_over_d"])
    source_step = int(records[frame_index]["step"])
    supports: list[dict] = []
    for other_index in range(
        max(0, frame_index - lookaround),
        min(len(records), frame_index + lookaround + 1),
    ):
        if other_index == frame_index:
            continue
        other_step = int(records[other_index]["step"])
        delta_t = (other_step - source_step) * inlet_velocity / diameter
        maximum_distance = maximum_speed * abs(delta_t)
        for detection in records[other_index]["base_detections"]:
            if int(detection["sign"]) != int(candidate["sign"]):
                continue
            dx = float(detection["x"]) - float(candidate["x"])
            dy = float(detection["y"]) - float(candidate["y"])
            if math.hypot(dx, dy) > maximum_distance:
                continue
            if delta_t > 0.0 and dx < -upstream_tolerance:
                continue
            if delta_t < 0.0 and dx > upstream_tolerance:
                continue
            supports.append({
                "frame_index": other_index,
                "source_step": other_step,
                "delta_t_u_over_d": delta_t,
                "distance_over_d": math.hypot(dx, dy),
                "x_over_d": float(detection["x"]),
                "y_over_d": float(detection["y"]),
            })
            break
    return supports


def recover(records: list[dict], cfg: dict, protocol: dict) -> list[dict]:
    """Return an audit and attach non-propagating temporal recoveries to records.

    Only the original spatially accepted detections provide temporal evidence;
    recovered candidates never support additional recoveries in the same pass.
    This prevents a single weak candidate from creating a self-sustaining track.
    """
    minimum_physics = int(cfg["minimum_spatial_physics_support"])
    minimum_temporal = int(cfg["minimum_temporal_support"])
    wall_radius = float(cfg["minimum_wall_distance_over_d"])
    duplicate_radius = float(cfg["same_frame_suppression_radius_over_d"])
    allowed_reasons = set(cfg["eligible_spatial_rejection_reasons"])
    audit: list[dict] = []
    for frame_index, record in enumerate(records):
        detections = list(record["base_detections"])
        for candidate in record["runtime"]["audit"]:
            if bool(candidate["accepted"]):
                continue
            count, checks = physics_support(candidate, cfg)
            supports = temporal_supports(candidate, frame_index, records, cfg, protocol)
            # Cylinder benchmarks infer wall clearance from the radial
            # coordinate. Cross-solver geometry adapters may instead attach a
            # precomputed outside-wall decision or exact mask distance. The
            # fallback preserves the frozen cylinder behaviour byte-for-byte.
            declared_outside_wall = candidate.get("outside_wall")
            wall_distance = candidate.get("wall_distance_over_d")
            if declared_outside_wall is not None:
                outside_wall = bool(declared_outside_wall)
            else:
                if wall_distance is None:
                    wall_distance = math.hypot(
                        float(candidate["x"]), float(candidate["y"])
                    )
                outside_wall = float(wall_distance) >= wall_radius
            eligible_reason = str(candidate["rejection_reason"]) in allowed_reasons
            duplicate = any(
                int(row["sign"]) == int(candidate["sign"])
                and math.hypot(
                    float(row["x"]) - float(candidate["x"]),
                    float(row["y"]) - float(candidate["y"]),
                ) < duplicate_radius
                for row in detections
            )
            accepted = bool(
                count >= minimum_physics
                and len(supports) >= minimum_temporal
                and outside_wall
                and eligible_reason
                and not duplicate
            )
            audit.append({
                "frame_index": frame_index,
                "source_step": int(record["step"]),
                "x_over_d": float(candidate["x"]),
                "y_over_d": float(candidate["y"]),
                "rotation_sign": int(candidate["sign"]),
                "original_rejection_reason": str(candidate["rejection_reason"]),
                "spatial_physics_support": count,
                **checks,
                "temporal_support": len(supports),
                "support_frames": ";".join(str(row["frame_index"]) for row in supports),
                "outside_wall": outside_wall,
                "eligible_rejection_reason": eligible_reason,
                "same_frame_duplicate": duplicate,
                "temporally_recovered": accepted,
            })
            if accepted:
                recovered = dict(candidate)
                recovered["accepted"] = True
                recovered["rejection_reason"] = "accepted_by_physics_gated_temporal_support"
                recovered["temporally_recovered"] = True
                recovered["temporal_support"] = supports
                detections.append(recovered)
        record["detections"] = detections
    return audit
