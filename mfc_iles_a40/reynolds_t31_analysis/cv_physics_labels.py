#!/usr/bin/env python3
"""Physics-derived weak labels for the MFC computer-vision dataset.

The vortex catalogue follows the definitions frozen in
``research/dart_cfd_pilot`` Stage 8: signed vorticity, swirling strength,
Q, Omega ratio, and Graftieaux Gamma2.  The labels are deliberately described
as physics-derived weak labels rather than hand-annotated ground truth.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np


AIRFOIL_HALF_HEIGHT = 0.0702704174
VORTEX_CONFIG = {
    "criterion_quantile": 0.985,
    "minimum_omega_ratio": 0.52,
    "minimum_absolute_gamma2": 0.70,
    "minimum_criterion_support": 3,
    "gamma2_radius_cells": 2,
    "minimum_core_separation": 0.08,
    "maximum_cores_per_frame": 80,
    "maximum_track_gap_frames": 2,
    "maximum_reference_displacement": 0.24,
    "strength_continuity_weight": 0.15,
}


def geometry_fluid_mask(x: np.ndarray, y: np.ndarray, guard_cells: float = 3.0) -> np.ndarray:
    dx = float(np.min(np.diff(x)))
    dy = float(np.min(np.diff(y)))
    pad = guard_cells * max(dx, dy)
    xx, yy = x[:, None], y[None, :]
    clipped = np.clip(xx, 0.0, 1.0)
    half = AIRFOIL_HALF_HEIGHT * (1.0 - np.abs(2.0 * clipped - 1.0))
    return ~(
        (xx >= -pad)
        & (xx <= 1.0 + pad)
        & (np.abs(yy) <= half + pad)
    )


def graftieaux_gamma2(
    x: np.ndarray,
    y: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    radius: int,
) -> np.ndarray:
    """Compute Gamma2 on a circular fixed-grid stencil without SciPy."""

    gamma = np.zeros_like(u, dtype=np.float32)
    count = np.zeros_like(u, dtype=np.float32)
    ubar = np.zeros_like(u, dtype=np.float32)
    vbar = np.zeros_like(v, dtype=np.float32)
    nbar = np.zeros_like(u, dtype=np.float32)
    offsets = [
        (di, dj)
        for di in range(-radius, radius + 1)
        for dj in range(-radius, radius + 1)
        if di * di + dj * dj <= radius * radius
    ]
    for di, dj in offsets:
        src_i = slice(max(0, -di), min(u.shape[0], u.shape[0] - di))
        dst_i = slice(max(0, di), min(u.shape[0], u.shape[0] + di))
        src_j = slice(max(0, -dj), min(u.shape[1], u.shape[1] - dj))
        dst_j = slice(max(0, dj), min(u.shape[1], u.shape[1] + dj))
        ubar[dst_i, dst_j] += u[src_i, src_j]
        vbar[dst_i, dst_j] += v[src_i, src_j]
        nbar[dst_i, dst_j] += 1.0
    ubar /= np.maximum(nbar, 1.0)
    vbar /= np.maximum(nbar, 1.0)
    dx0 = float(np.median(np.diff(x)))
    dy0 = float(np.median(np.diff(y)))
    for di, dj in offsets:
        if di == 0 and dj == 0:
            continue
        src_i = slice(max(0, -di), min(u.shape[0], u.shape[0] - di))
        dst_i = slice(max(0, di), min(u.shape[0], u.shape[0] + di))
        src_j = slice(max(0, -dj), min(u.shape[1], u.shape[1] - dj))
        dst_j = slice(max(0, dj), min(u.shape[1], u.shape[1] + dj))
        du = u[src_i, src_j] - ubar[dst_i, dst_j]
        dv = v[src_i, src_j] - vbar[dst_i, dst_j]
        rx, ry = -di * dx0, -dj * dy0
        denominator = math.hypot(rx, ry) * np.sqrt(du * du + dv * dv) + 1.0e-14
        gamma[dst_i, dst_j] += (rx * dv - ry * du) / denominator
        count[dst_i, dst_j] += 1.0
    return gamma / np.maximum(count, 1.0)


def vortex_diagnostics(
    x: np.ndarray, y: np.ndarray, u: np.ndarray, v: np.ndarray
) -> dict[str, np.ndarray]:
    du_dx, du_dy = np.gradient(u, x, y, edge_order=2)
    dv_dx, dv_dy = np.gradient(v, x, y, edge_order=2)
    omega = dv_dx - du_dy
    trace = du_dx + dv_dy
    determinant = du_dx * dv_dy - du_dy * dv_dx
    discriminant = trace * trace - 4.0 * determinant
    lambda_ci = 0.5 * np.sqrt(np.maximum(-discriminant, 0.0))
    q_criterion = -0.5 * (
        du_dx * du_dx + dv_dy * dv_dy + 2.0 * du_dy * dv_dx
    )
    strain2 = (
        du_dx * du_dx
        + dv_dy * dv_dy
        + 0.5 * (du_dy + dv_dx) ** 2
    )
    rotation2 = 0.5 * omega * omega
    epsilon = 1.0e-12 * max(float(np.nanmax(strain2 + rotation2)), 1.0)
    omega_ratio = rotation2 / (rotation2 + strain2 + epsilon)
    gamma2 = graftieaux_gamma2(
        x, y, u, v, int(VORTEX_CONFIG["gamma2_radius_cells"])
    )
    return {
        "omega": np.asarray(omega, dtype=np.float32),
        "lambda_ci": np.asarray(lambda_ci, dtype=np.float32),
        "q": np.asarray(q_criterion, dtype=np.float32),
        "omega_ratio": np.asarray(omega_ratio, dtype=np.float32),
        "gamma2": np.asarray(gamma2, dtype=np.float32),
    }


def _quantile(values: np.ndarray, mask: np.ndarray, q: float, positive: bool = False) -> float:
    selected = values[mask & np.isfinite(values)]
    if positive:
        selected = selected[selected > 0.0]
    return float(np.quantile(selected, q)) if selected.size else math.inf


def extract_vortex_cores(
    x: np.ndarray,
    y: np.ndarray,
    fields: dict[str, np.ndarray],
    fluid: np.ndarray,
    config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    cfg = dict(VORTEX_CONFIG)
    if config:
        cfg.update(config)
    omega = fields["omega"]
    lambda_ci = fields["lambda_ci"]
    q_criterion = fields["q"]
    omega_ratio = fields["omega_ratio"]
    gamma2 = fields["gamma2"]
    # Match the Stage-8 common field of view used by the existing DART pilot.
    vortex_roi = (
        (x[:, None] >= 0.6201298701298701)
        & (x[:, None] <= 4.75)
        & (y[None, :] >= -0.12914691943127998)
        & (y[None, :] <= 3.8785545023696684)
    )
    rotating = fluid & vortex_roi & np.isfinite(omega) & (lambda_ci > 0.0)
    quantile = float(cfg["criterion_quantile"])
    thresholds = {
        "absolute_vorticity": _quantile(np.abs(omega), rotating, quantile),
        "lambda_ci": _quantile(lambda_ci, rotating, quantile, positive=True),
        "q": _quantile(q_criterion, rotating, quantile, positive=True),
        "omega_ratio": float(cfg["minimum_omega_ratio"]),
        "absolute_gamma2": float(cfg["minimum_absolute_gamma2"]),
    }
    criteria = [
        np.abs(omega) >= thresholds["absolute_vorticity"],
        lambda_ci >= thresholds["lambda_ci"],
        q_criterion >= thresholds["q"],
        omega_ratio >= thresholds["omega_ratio"],
        np.abs(gamma2) >= thresholds["absolute_gamma2"],
    ]
    support = sum(item.astype(np.int8) for item in criteria)
    candidate = (
        rotating
        & (support >= int(cfg["minimum_criterion_support"]))
        & (criteria[1] | criteria[2])
        & criteria[4]
    )
    indices = np.argwhere(candidate)
    if not indices.size:
        return [], thresholds
    scales = [
        max(thresholds["absolute_vorticity"], 1.0e-15),
        max(thresholds["lambda_ci"], 1.0e-15),
        max(thresholds["q"], 1.0e-15),
    ]
    score = (
        support / 5.0
        + 0.10 * np.clip(np.abs(omega) / scales[0], 0.0, 5.0)
        + 0.10 * np.clip(lambda_ci / scales[1], 0.0, 5.0)
        + 0.05 * np.clip(np.maximum(q_criterion, 0.0) / scales[2], 0.0, 5.0)
        + 0.05 * np.abs(gamma2)
    )
    order = sorted(
        indices.tolist(),
        key=lambda ij: (-float(score[tuple(ij)]), int(ij[0]), int(ij[1])),
    )
    accepted: list[dict[str, Any]] = []
    separation = float(cfg["minimum_core_separation"])
    for i, j in order:
        xp, yp = float(x[i]), float(y[j])
        if any(
            math.hypot(xp - row["x_physical"], yp - row["y_physical"])
            < separation
            for row in accepted
        ):
            continue
        accepted.append(
            {
                "x_physical": xp,
                "y_physical": yp,
                "rotation_sign": 1 if omega[i, j] >= 0.0 else -1,
                "omega": float(omega[i, j]),
                "lambda_ci": float(lambda_ci[i, j]),
                "q": float(q_criterion[i, j]),
                "omega_ratio": float(omega_ratio[i, j]),
                "gamma2": float(gamma2[i, j]),
                "criterion_support": int(support[i, j]),
                "confidence": float(min(score[i, j] / 2.0, 1.0)),
            }
        )
        if len(accepted) >= int(cfg["maximum_cores_per_frame"]):
            break
    return accepted, thresholds


def associate_vortex_cores(
    cores: list[dict[str, Any]],
    frame_index: int,
    tracks: dict[int, list[dict[str, Any]]],
    next_id: int,
    config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]], int]:
    cfg = dict(VORTEX_CONFIG)
    if config:
        cfg.update(config)
    maximum_gap = int(cfg["maximum_track_gap_frames"])
    maximum_distance = float(cfg["maximum_reference_displacement"])
    weight = float(cfg["strength_continuity_weight"])
    candidates: list[tuple[float, int, int, float]] = []
    for core_index, core in enumerate(cores):
        for track_id, history in tracks.items():
            last = history[-1]
            gap = frame_index - int(last["frame_index"])
            if (
                gap < 1
                or gap > maximum_gap + 1
                or int(core["rotation_sign"]) != int(last["rotation_sign"])
            ):
                continue
            if len(history) >= 2:
                previous = history[-2]
                history_gap = max(
                    int(last["frame_index"]) - int(previous["frame_index"]), 1
                )
                px = float(last["x_physical"]) + gap * (
                    float(last["x_physical"]) - float(previous["x_physical"])
                ) / history_gap
                py = float(last["y_physical"]) + gap * (
                    float(last["y_physical"]) - float(previous["y_physical"])
                ) / history_gap
            else:
                px, py = float(last["x_physical"]), float(last["y_physical"])
            distance = math.hypot(
                float(core["x_physical"]) - px,
                float(core["y_physical"]) - py,
            )
            gate = maximum_distance * math.sqrt(gap)
            if distance > gate:
                continue
            strength_change = abs(
                math.log(
                    (abs(float(core["omega"])) + 1.0e-12)
                    / (abs(float(last["omega"])) + 1.0e-12)
                )
            )
            candidates.append(
                (distance / gate + weight * strength_change, core_index, track_id, distance)
            )
    assignments: dict[int, tuple[int, float, float]] = {}
    used_cores: set[int] = set()
    used_tracks: set[int] = set()
    for cost, core_index, track_id, distance in sorted(candidates):
        if core_index in used_cores or track_id in used_tracks:
            continue
        assignments[core_index] = (track_id, cost, distance)
        used_cores.add(core_index)
        used_tracks.add(track_id)
    output: list[dict[str, Any]] = []
    for core_index, core in enumerate(cores):
        if core_index in assignments:
            track_id, cost, distance = assignments[core_index]
        else:
            track_id, cost, distance = next_id, 0.0, 0.0
            next_id += 1
            tracks[track_id] = []
        row = dict(
            core,
            reference_id=f"P{track_id:05d}",
            frame_index=frame_index,
            association_cost=float(cost),
            prediction_error=float(distance),
        )
        tracks.setdefault(track_id, []).append(row)
        output.append(row)
    # Discard inactive track state so a large temporal gap cannot create an ID
    # across intentionally pruned periods.
    active = {
        track_id: history
        for track_id, history in tracks.items()
        if frame_index - int(history[-1]["frame_index"]) <= maximum_gap + 1
    }
    return output, active, next_id


def bow_shock_labels(
    x: np.ndarray,
    y: np.ndarray,
    grad_rho: np.ndarray,
    fluid: np.ndarray,
    alpha_deg: float = 40.0,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Return a fitted shock ribbon, one-pixel ridge, and fit metadata."""

    alpha = math.radians(alpha_deg)
    n_values = np.linspace(-0.35, 0.35, 81)
    s_values = np.linspace(-1.25, -0.02, 900)
    ridge_s = np.full(n_values.size, np.nan)
    strength = np.full(n_values.size, np.nan)
    for index, normal in enumerate(n_values):
        x_ray = s_values * math.cos(alpha) - normal * math.sin(alpha)
        y_ray = s_values * math.sin(alpha) + normal * math.cos(alpha)
        inside = (
            (x_ray >= x[0])
            & (x_ray <= x[-1])
            & (y_ray >= y[0])
            & (y_ray <= y[-1])
        )
        if not np.any(inside):
            continue
        ix = np.clip(np.searchsorted(x, x_ray[inside]), 1, len(x) - 1)
        ix -= np.abs(x[ix - 1] - x_ray[inside]) <= np.abs(x[ix] - x_ray[inside])
        iy = np.clip(np.searchsorted(y, y_ray[inside]), 1, len(y) - 1)
        iy -= np.abs(y[iy - 1] - y_ray[inside]) <= np.abs(y[iy] - y_ray[inside])
        signal = grad_rho[ix, iy]
        valid = fluid[ix, iy] & np.isfinite(signal)
        if not np.any(valid):
            continue
        candidates = np.flatnonzero(valid)
        local = int(candidates[np.argmax(signal[valid])])
        original = np.flatnonzero(inside)[local]
        ridge_s[index] = s_values[original]
        strength[index] = signal[local]
    finite = strength[np.isfinite(strength)]
    empty = np.zeros((len(x), len(y)), dtype=np.uint8)
    if finite.size < 12 or float(np.max(finite)) <= np.finfo(float).eps:
        return empty, empty.copy(), {"status": "NOT_DETECTED"}
    floor = max(float(np.percentile(finite, 30.0)), 0.04 * float(np.max(finite)))
    accepted = (
        np.isfinite(ridge_s)
        & np.isfinite(strength)
        & (strength >= floor)
        & (np.abs(n_values) <= 0.24)
    )
    if np.count_nonzero(accepted) < 10:
        return empty, empty.copy(), {"status": "NOT_DETECTED"}
    weights = np.maximum(strength[accepted], 0.0)
    design = np.column_stack(
        [np.ones(np.count_nonzero(accepted)), n_values[accepted]]
    )
    root_weight = np.sqrt(weights)
    intercept, slope = np.linalg.lstsq(
        design * root_weight[:, None], ridge_s[accepted] * root_weight, rcond=None
    )[0]
    xx, yy = x[:, None], y[None, :]
    s_grid = xx * math.cos(alpha) + yy * math.sin(alpha)
    n_grid = -xx * math.sin(alpha) + yy * math.cos(alpha)
    distance = np.abs(s_grid - (intercept + slope * n_grid))
    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    ridge = (
        (distance <= 0.75 * max(dx, dy))
        & (np.abs(n_grid) <= 0.24)
        & fluid
    )
    ribbon = (
        (distance <= 2.5 * max(dx, dy))
        & (np.abs(n_grid) <= 0.28)
        & (grad_rho >= floor)
        & fluid
    )
    fitted = intercept + slope * n_values[accepted]
    fit_rms = float(
        np.sqrt(np.average((ridge_s[accepted] - fitted) ** 2, weights=weights))
    )
    return (
        ribbon.astype(np.uint8),
        ridge.astype(np.uint8),
        {
            "status": "PASS",
            "stand_off_over_c": float(-intercept),
            "fit_slope_ds_dn": float(slope),
            "fit_rms_over_c": fit_rms,
            "strength_floor": floor,
            "accepted_rays": int(np.count_nonzero(accepted)),
        },
    )


def vortex_heatmaps(
    x: np.ndarray,
    y: np.ndarray,
    cores: list[dict[str, Any]],
    sigma: float = 0.045,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xx, yy = x[:, None], y[None, :]
    positive = np.zeros((len(x), len(y)), dtype=np.float32)
    negative = np.zeros_like(positive)
    instances = np.zeros((len(x), len(y)), dtype=np.uint16)
    for instance, core in enumerate(cores, start=1):
        distance2 = (
            (xx - float(core["x_physical"])) ** 2
            + (yy - float(core["y_physical"])) ** 2
        )
        heat = np.exp(-0.5 * distance2 / (sigma * sigma)).astype(np.float32)
        target = positive if int(core["rotation_sign"]) > 0 else negative
        np.maximum(target, heat, out=target)
        instances[distance2 <= (0.06**2)] = instance
    return positive, negative, instances
