#!/usr/bin/env python3
"""Shock-ridge-aware CMCD development audit on raw SU2 restart fields.

The alpha=40 SU2 case is an unlabelled development diagnostic.  It may expose
failure modes and define a revised filter, but it is neither a zero-vortex
negative control nor an independent validation case.
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
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
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


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_su2_restart(archive: zipfile.ZipFile, member: str, gamma: float) -> dict[str, np.ndarray]:
    columns: dict[str, list[float]] = {
        "x": [], "y": [], "rho": [], "u": [], "v": [], "pressure": [],
    }
    with archive.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            rho = float(row["Density"])
            mx = float(row["Momentum_x"])
            my = float(row["Momentum_y"])
            energy = float(row["Energy"])
            columns["x"].append(float(row["x"]))
            columns["y"].append(float(row["y"]))
            columns["rho"].append(rho)
            columns["u"].append(mx / rho)
            columns["v"].append(my / rho)
            columns["pressure"].append(
                (gamma - 1.0) * (energy - 0.5 * (mx * mx + my * my) / rho)
            )
    return {name: np.asarray(values, dtype=float) for name, values in columns.items()}


def periodic_gradient(array: np.ndarray) -> np.ndarray:
    return 0.5 * (np.roll(array, -1, axis=1) - np.roll(array, 1, axis=1))


def derive_native_ogrid_fields(raw: dict[str, np.ndarray], radial: int, circumferential: int) -> dict[str, np.ndarray]:
    expected = radial * circumferential
    if raw["x"].size != expected:
        raise RuntimeError(f"SU2 O-grid size mismatch: expected {expected}, found {raw['x'].size}")
    shape = (radial, circumferential)
    x = raw["x"].reshape(shape)
    y = raw["y"].reshape(shape)
    u = raw["u"].reshape(shape)
    v = raw["v"].reshape(shape)
    xr = np.gradient(x, axis=0, edge_order=2)
    yr = np.gradient(y, axis=0, edge_order=2)
    ur = np.gradient(u, axis=0, edge_order=2)
    vr = np.gradient(v, axis=0, edge_order=2)
    xt = periodic_gradient(x)
    yt = periodic_gradient(y)
    ut = periodic_gradient(u)
    vt = periodic_gradient(v)
    determinant = xr * yt - xt * yr
    valid = np.abs(determinant) > 1.0e-14
    dux = np.divide(ur * yt - ut * yr, determinant, out=np.zeros_like(u), where=valid)
    duy = np.divide(-ur * xt + ut * xr, determinant, out=np.zeros_like(u), where=valid)
    dvx = np.divide(vr * yt - vt * yr, determinant, out=np.zeros_like(v), where=valid)
    dvy = np.divide(-vr * xt + vt * xr, determinant, out=np.zeros_like(v), where=valid)
    omega = dvx - duy
    divergence = dux + dvy
    velocity_gradient_determinant = dux * dvy - duy * dvx
    discriminant = divergence * divergence - 4.0 * velocity_gradient_determinant
    lci = 0.5 * np.sqrt(np.maximum(-discriminant, 0.0))
    sxy = 0.5 * (duy + dvx)
    strain2 = dux * dux + dvy * dvy + 2.0 * sxy * sxy
    rotation2 = 0.5 * omega * omega
    q = np.maximum(0.5 * (rotation2 - strain2), 0.0)
    return {
        "u": u.ravel(), "v": v.ravel(), "rho": raw["rho"],
        "pressure": raw["pressure"], "omega": omega.ravel(),
        "divergence": divergence.ravel(), "lci": lci.ravel(), "q": q.ravel(),
    }


def structured_ogrid_triangles(radial: int, circumferential: int) -> np.ndarray:
    """Return genuine neighboring-cell connectivity for a logical SU2 O-grid."""
    if radial < 2 or circumferential < 3:
        raise RuntimeError(
            "SU2 O-grid dimensions must have at least two radial and three "
            "circumferential nodes"
        )
    expected = radial * circumferential
    cells = np.arange(expected, dtype=np.int64).reshape(radial, circumferential)
    lower = cells[:-1]
    upper = cells[1:]
    lower_next = np.roll(lower, -1, axis=1)
    upper_next = np.roll(upper, -1, axis=1)
    return np.concatenate(
        (
            np.column_stack((lower.ravel(), upper.ravel(), upper_next.ravel())),
            np.column_stack((lower.ravel(), upper_next.ravel(), lower_next.ravel())),
        ),
        axis=0,
    )


def structured_ogrid_triangulation(
    coordinates: np.ndarray,
    radial: int,
    circumferential: int,
):
    """Triangulate only genuine neighboring cells of the logical SU2 O-grid.

    A point-cloud Delaunay triangulation fills the airfoil hole and may connect
    distant nodes across the wrapped O-grid.  Those non-mesh edges create
    triangular velocity-gradient artifacts that look like vortex cores.  The
    explicit connectivity below preserves both the inner boundary and the
    periodic circumferential seam.
    """
    import matplotlib.tri as mtri

    expected = radial * circumferential
    if coordinates.shape != (expected, 2):
        raise RuntimeError(
            f"SU2 O-grid coordinate size mismatch: expected {(expected, 2)}, "
            f"found {coordinates.shape}"
        )
    triangles = structured_ogrid_triangles(radial, circumferential)
    return mtri.Triangulation(coordinates[:, 0], coordinates[:, 1], triangles)


def interpolate_native_fields(
    triangulation,
    native: dict[str, np.ndarray],
    x: np.ndarray,
    y: np.ndarray,
) -> dict[str, np.ndarray]:
    from matplotlib.tri import LinearTriInterpolator

    interpolators = {
        name: LinearTriInterpolator(triangulation, values)
        for name, values in native.items()
    }
    fields = {name: np.empty((x.size, y.size), dtype=float) for name in native}
    for i0 in range(0, x.size, 96):
        i1 = min(i0 + 96, x.size)
        xx, yy = np.meshgrid(x[i0:i1], y, indexing="ij")
        for name, interpolator in interpolators.items():
            interpolated = interpolator(xx, yy)
            fields[name][i0:i1] = np.ma.filled(interpolated, np.nan)
    return fields


def finish_raster_fields(
    x: np.ndarray,
    y: np.ndarray,
    fields: dict[str, np.ndarray],
    geometry_fluid: np.ndarray,
    gaussian_sigmas: list[float],
    gamma: float,
) -> dict:
    from scipy.ndimage import distance_transform_edt, gaussian_filter

    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    grid_scale = float(math.sqrt(abs(dx * dy)))
    q_scales = [gaussian_filter(fields["q"], sigma=float(value), mode="nearest") for value in gaussian_sigmas]
    smooth = gaussian_filter(fields["q"], sigma=1.0, mode="nearest")
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
    px, py = np.gradient(fields["pressure"], x, y, edge_order=2)
    entropy = np.log(
        np.maximum(fields["pressure"], 1.0e-300)
        / np.maximum(fields["rho"], 1.0e-300) ** gamma
    )
    sx, sy = np.gradient(entropy, x, y, edge_order=2)
    pressure_jump = grid_scale * np.hypot(px, py) / np.maximum(np.abs(fields["pressure"]), 1.0e-300)
    entropy_jump = grid_scale * np.hypot(sx, sy)
    return {
        **fields,
        "wall_distance": distance_transform_edt(geometry_fluid, sampling=(abs(dx), abs(dy))),
        "q_scales": q_scales,
        "hessian_compactness": hessian_compactness,
        "pressure_jump_sensor": pressure_jump,
        "entropy_jump_sensor": entropy_jump,
        "grid_scale": grid_scale,
    }


def ring_winding_features(snapshot: dict, candidate: dict, radius_cells: float, samples: int) -> dict:
    from scipy.ndimage import map_coordinates

    x, y = snapshot["x"], snapshot["y"]
    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    theta = np.linspace(0.0, 2.0 * math.pi, samples, endpoint=False)
    ii = (float(candidate["x"]) + radius_cells * dx * np.cos(theta) - x[0]) / dx
    jj = (float(candidate["y"]) + radius_cells * dy * np.sin(theta) - y[0]) / dy
    coordinates = np.vstack((ii, jj))
    u = map_coordinates(snapshot["u"], coordinates, order=1, mode="nearest")
    v = map_coordinates(snapshot["v"], coordinates, order=1, mode="nearest")
    fluid = map_coordinates(snapshot["fluid"].astype(float), coordinates, order=0, mode="nearest") > 0.5
    valid_fraction = float(np.mean(fluid))
    if valid_fraction < 0.9:
        return {
            "radius_cells": radius_cells, "valid_fraction": valid_fraction,
            "absolute_winding": 0.0, "tangential_coherence": 0.0,
            "radial_to_tangential": float("inf"), "pass": False,
        }
    du = u - float(np.mean(u))
    dv = v - float(np.mean(v))
    phase = np.angle(du + 1j * dv)
    increments = np.angle(np.exp(1j * (np.roll(phase, -1) - phase)))
    # The velocity-vector phase winds once around both clockwise and
    # counter-clockwise cores.  Rotation direction is encoded by the signed
    # tangential velocity below; multiplying the phase winding by the
    # vorticity sign would reject every clockwise vortex.
    absolute_winding = abs(float(np.sum(increments) / (2.0 * math.pi)))
    tangential = -du * np.sin(theta) + dv * np.cos(theta)
    radial = du * np.cos(theta) + dv * np.sin(theta)
    tangential_coherence = float(np.mean(int(candidate["sign"]) * tangential > 0.0))
    radial_to_tangential = float(
        np.median(np.abs(radial))
        / max(float(np.median(np.abs(tangential))), 1.0e-300)
    )
    return {
        "radius_cells": radius_cells,
        "valid_fraction": valid_fraction,
        "absolute_winding": absolute_winding,
        "tangential_coherence": tangential_coherence,
        "radial_to_tangential": radial_to_tangential,
    }


def winding_pass(features: dict, cfg: dict) -> bool:
    return bool(
        features["valid_fraction"] >= float(cfg["minimum_ring_valid_fraction"])
        and features["absolute_winding"] >= float(cfg["minimum_absolute_winding"])
        and features["tangential_coherence"] >= float(cfg["minimum_tangential_coherence"])
        and features["radial_to_tangential"] <= float(cfg["maximum_winding_radial_to_tangential"])
    )


def closed_q_island(snapshot: dict, candidate: dict, cfg: dict) -> dict:
    from scipy.ndimage import label

    radius = int(cfg["q_island_radius_cells"])
    maximum_radius = int(cfg.get("q_island_maximum_radius_cells", radius))
    if maximum_radius < radius:
        raise ValueError("q_island_maximum_radius_cells must not be smaller than the initial radius")
    fraction = float(cfg["q_island_peak_fraction"])
    i, j = int(candidate["grid_i"]), int(candidate["grid_j"])
    q = snapshot["q"]
    component = None
    touches_boundary = True
    while True:
        i0, i1 = max(0, i - radius), min(q.shape[0], i + radius + 1)
        j0, j1 = max(0, j - radius), min(q.shape[1], j + radius + 1)
        patch = q[i0:i1, j0:j1]
        components, _ = label(
            patch >= fraction * max(float(q[i, j]), 1.0e-300),
            structure=np.ones((3, 3), dtype=int),
        )
        component_id = int(components[i - i0, j - j0])
        if component_id == 0:
            return {
                "closed": False, "area_cells": 0, "aspect_ratio": float("inf"),
                "analysis_radius_cells": radius, "pass": False,
            }
        component = components == component_id
        edge_hits = (
            bool(np.any(component[0, :])), bool(np.any(component[-1, :])),
            bool(np.any(component[:, 0])), bool(np.any(component[:, -1])),
        )
        touches_boundary = any(edge_hits)
        touches_domain_boundary = bool(
            (i0 == 0 and edge_hits[0]) or (i1 == q.shape[0] and edge_hits[1])
            or (j0 == 0 and edge_hits[2]) or (j1 == q.shape[1] and edge_hits[3])
        )
        if not touches_boundary or touches_domain_boundary or radius >= maximum_radius:
            break
        radius = min(maximum_radius, max(radius + 1, int(math.ceil(1.5 * radius))))

    assert component is not None
    points = np.argwhere(component)
    area = int(points.shape[0])
    aspect = float("inf")
    if area >= 3:
        eigenvalues = np.linalg.eigvalsh(np.cov(points.T))
        aspect = float(math.sqrt(
            max(float(eigenvalues[-1]), 1.0e-12)
            / max(float(eigenvalues[0]), 1.0e-12)
        ))
    passed = bool(
        not touches_boundary
        and area >= int(cfg["minimum_q_island_area_cells"])
        and aspect <= float(cfg["maximum_q_island_aspect_ratio"])
    )
    return {
        "closed": not touches_boundary, "area_cells": area, "aspect_ratio": aspect,
        "analysis_radius_cells": radius, "pass": passed,
    }


def pressure_core_support(snapshot: dict, candidate: dict, cfg: dict) -> dict:
    from scipy.ndimage import map_coordinates

    x, y = snapshot["x"], snapshot["y"]
    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    i, j = int(candidate["grid_i"]), int(candidate["grid_j"])
    search = int(cfg["pressure_minimum_search_radius_cells"])
    i0, i1 = max(0, i - search), min(len(x), i + search + 1)
    j0, j1 = max(0, j - search), min(len(y), j + search + 1)
    local = snapshot["pressure"][i0:i1, j0:j1]
    offset = np.unravel_index(int(np.nanargmin(local)), local.shape)
    ic, jc = i0 + int(offset[0]), j0 + int(offset[1])
    p_core = float(snapshot["pressure"][ic, jc])
    theta = np.linspace(0.0, 2.0 * math.pi, int(cfg["winding_samples"]), endpoint=False)
    rings: list[dict] = []
    for radius_cells in cfg["winding_radii_cells"]:
        ii = (float(x[ic]) + float(radius_cells) * dx * np.cos(theta) - x[0]) / dx
        jj = (float(y[jc]) + float(radius_cells) * dy * np.sin(theta) - y[0]) / dy
        ring = map_coordinates(snapshot["pressure"], np.vstack((ii, jj)), order=1, mode="nearest")
        below_fraction = float(np.mean(p_core < ring))
        median_drop = float(np.median(ring) - p_core)
        rings.append({
            "radius_cells": float(radius_cells),
            "core_below_ring_fraction": below_fraction,
            "median_pressure_drop": median_drop,
            "pass": bool(
                below_fraction >= float(cfg["minimum_pressure_below_ring_fraction"])
                and median_drop > 0.0
            ),
        })
    support = sum(bool(row["pass"]) for row in rings)
    offset_cells = float(math.hypot(ic - i, jc - j))
    return {
        "minimum_x": float(x[ic]), "minimum_y": float(y[jc]),
        "offset_cells": offset_cells,
        "ring_support": support, "rings": rings,
        "pass": bool(
            support >= int(cfg["minimum_pressure_ring_support"])
            and offset_cells <= float(cfg["maximum_pressure_minimum_offset_cells"])
        ),
    }


def scale_adaptive_pressure_support(pressure: dict, q_island: dict, cfg: dict) -> dict:
    """Scale the allowed Q-to-pressure displacement with coherent-core size.

    The original frozen SRA-CMCD configuration omits the optional scale keys,
    so its decision is unchanged.  A derived method may explicitly enable the
    scale law while retaining the original absolute lower bound.
    """
    result = dict(pressure)
    baseline = float(cfg["maximum_pressure_minimum_offset_cells"])
    fraction = float(cfg.get("pressure_offset_equivalent_radius_fraction", 0.0))
    maximum = float(cfg.get("maximum_scale_adaptive_pressure_offset_cells", baseline))
    equivalent_radius = math.sqrt(max(float(q_island["area_cells"]), 0.0) / math.pi)
    allowed = max(baseline, min(maximum, fraction * equivalent_radius))
    result["equivalent_q_radius_cells"] = equivalent_radius
    result["allowed_offset_cells"] = allowed
    result["pass"] = bool(
        int(result["ring_support"]) >= int(cfg["minimum_pressure_ring_support"])
        and float(result["offset_cells"]) <= allowed
    )
    return result


def build_shock_ridge_mask(snapshot: dict, cfg: dict) -> tuple[np.ndarray, np.ndarray]:
    from scipy.ndimage import distance_transform_edt

    mask = (
        snapshot["fluid"]
        & (snapshot["pressure_jump_sensor"] >= float(cfg["minimum_pressure_jump_per_cell"]))
        & (snapshot["entropy_jump_sensor"] >= float(cfg["minimum_entropy_jump_per_cell"]))
    )
    distance = distance_transform_edt(~mask)
    return mask, distance


def revised_decision(
    q_island: dict,
    winding_support: int,
    pressure: dict,
    shock_distance_cells: float,
    cfg: dict,
) -> tuple[bool, str]:
    if not bool(q_island["pass"]):
        return False, "open_or_elongated_q_island"
    if winding_support < int(cfg["minimum_winding_ring_support"]):
        return False, "insufficient_multiradius_winding"
    if not bool(pressure["pass"]):
        return False, "pressure_minimum_not_corroborated"
    if shock_distance_cells <= float(cfg["maximum_shock_ridge_distance_cells"]):
        return False, "thermodynamic_shock_ridge_proximity"
    return True, "accepted"


def suppress_subordinate_same_sign_peaks(rows: list[dict], cfg: dict) -> list[dict]:
    """Reject weak satellite peaks without merging equal-strength close cores."""
    radius = float(cfg["subordinate_peak_radius_cells"])
    score_fraction = float(cfg["subordinate_peak_maximum_score_fraction"])
    minimum_pressure_offset = float(cfg["subordinate_peak_minimum_pressure_offset_cells"])
    retained: list[dict] = []
    for row in sorted(rows, key=lambda item: float(item["score"]), reverse=True):
        if not row["accepted"]:
            continue
        subordinate = any(
            int(row["sign"]) == int(stronger["sign"])
            and math.hypot(
                int(row["grid_i"]) - int(stronger["grid_i"]),
                int(row["grid_j"]) - int(stronger["grid_j"]),
            ) <= radius
            and float(row["score"]) < score_fraction * float(stronger["score"])
            and float(row["pressure_core"]["offset_cells"]) > minimum_pressure_offset
            for stronger in retained
        )
        if subordinate:
            row["accepted"] = False
            row["rejection_reason"] = "subordinate_same_sign_peak"
        else:
            retained.append(row)
    return rows


def rescue_corroborated_opposite_sign_pairs(rows: list[dict], cfg: dict) -> list[dict]:
    """Allow pressure-minimum displacement only for a resolved vortex dipole."""
    radius = float(cfg["opposite_sign_pair_radius_cells"])
    minimum_ratio = float(cfg["minimum_opposite_sign_pair_score_ratio"])
    maximum_offset = float(cfg["maximum_opposite_sign_pair_pressure_offset_cells"])
    minimum_ring_support = int(cfg["minimum_pressure_ring_support"])
    minimum_winding_support = int(cfg["minimum_winding_ring_support"])
    eligible = [
        row for row in rows
        if row["rejection_reason"] == "pressure_minimum_not_corroborated"
        and row["q_island"]["pass"]
        and int(row["winding_support"]) >= minimum_winding_support
        and int(row["pressure_core"]["ring_support"]) >= minimum_ring_support
        and float(row["pressure_core"]["offset_cells"]) <= maximum_offset
        and float(row["shock_ridge_distance_cells"])
        > float(cfg["maximum_shock_ridge_distance_cells"])
    ]
    for row in eligible:
        partner = any(
            int(row["sign"]) == -int(other["sign"])
            and math.hypot(
                int(row["grid_i"]) - int(other["grid_i"]),
                int(row["grid_j"]) - int(other["grid_j"]),
            ) <= radius
            and min(float(row["score"]), float(other["score"]))
            / max(float(row["score"]), float(other["score"]), 1.0e-300) >= minimum_ratio
            for other in eligible
            if other is not row
        )
        if partner:
            row["accepted"] = True
            row["rejection_reason"] = "accepted_corroborated_opposite_sign_pair"
            if "pre_shock_accepted" in row:
                row["pre_shock_accepted"] = True
    return rows


def draw_physical(path: Path, snapshot: dict, original: list[dict], audit: list[dict], shock_mask: np.ndarray, cfg: dict) -> None:
    import matplotlib.pyplot as plt

    field = np.where(snapshot["fluid"], snapshot["omega"], np.nan)
    limit = max(float(np.nanpercentile(np.abs(field), 99.5)), 1.0e-8)
    levels = np.linspace(-limit, limit, 81)
    fig, axes = plt.subplots(1, 4, figsize=(21, 5.2), sharex=True, sharey=True, constrained_layout=True)
    titles = [
        "(a) SU2 vorticity", "(b) Frozen AA-ACB-CMCD",
        "(c) Closed-loop + pressure", "(d) Shock-ridge-aware CMCD",
    ]
    corroborated = [row for row in audit if row["pre_shock_accepted"]]
    final = [row for row in audit if row["accepted"]]
    shock_rejected = [row for row in audit if row["rejection_reason"] == "thermodynamic_shock_ridge_proximity"]
    for axis, title in zip(axes, titles):
        axis.contourf(snapshot["x"], snapshot["y"], field.T, levels=levels, cmap="RdBu_r", extend="both")
        axis.set_title(title)
        axis.set_aspect("equal")
        axis.set_xlabel("x/c")
        axis.set_xlim(*cfg["figure_xlim"])
        axis.set_ylim(*cfg["figure_ylim"])
    axes[0].set_ylabel("y/c")
    if original:
        axes[1].scatter([r["x"] for r in original], [r["y"] for r in original], s=46, facecolors="none", edgecolors="#ffe000", linewidths=1.2)
    if corroborated:
        axes[2].scatter([r["x"] for r in corroborated], [r["y"] for r in corroborated], s=62, facecolors="none", edgecolors="#00d070", linewidths=1.6)
    axes[3].contour(snapshot["x"], snapshot["y"], shock_mask.T.astype(float), levels=[0.5], colors="#c000ff", linewidths=0.8)
    if final:
        axes[3].scatter([r["x"] for r in final], [r["y"] for r in final], s=70, facecolors="none", edgecolors="#00d070", linewidths=1.8)
    if shock_rejected:
        axes[3].scatter([r["x"] for r in shock_rejected], [r["y"] for r in shock_rejected], marker="x", s=55, c="black", linewidths=1.2)
    fig.savefig(path, dpi=240, bbox_inches="tight")
    plt.close(fig)


def draw_shock_zoom(path: Path, snapshot: dict, audit: list[dict], shock_mask: np.ndarray) -> None:
    import matplotlib.pyplot as plt

    candidates = [row for row in audit if row["rejection_reason"] == "thermodynamic_shock_ridge_proximity"]
    if not candidates:
        return
    candidate = min(candidates, key=lambda row: float(row["shock_ridge_distance_cells"]))
    x0, y0 = float(candidate["x"]), float(candidate["y"])
    half_width = 0.18
    x, y = snapshot["x"], snapshot["y"]
    ix = (x >= x0 - half_width) & (x <= x0 + half_width)
    iy = (y >= y0 - half_width) & (y <= y0 + half_width)
    field = np.where(snapshot["fluid"][np.ix_(ix, iy)], snapshot["omega"][np.ix_(ix, iy)], np.nan)
    limit = max(float(np.nanpercentile(np.abs(field), 99.0)), 1.0e-8)
    fig, axis = plt.subplots(figsize=(6.2, 5.5), constrained_layout=True)
    axis.contourf(x[ix], y[iy], field.T, levels=np.linspace(-limit, limit, 81), cmap="RdBu_r", extend="both")
    axis.contour(x[ix], y[iy], shock_mask[np.ix_(ix, iy)].T.astype(float), levels=[0.5], colors="#c000ff", linewidths=1.5)
    i, j = int(candidate["grid_i"]), int(candidate["grid_j"])
    residual_u = snapshot["u"][np.ix_(ix, iy)] - float(snapshot["u"][i, j])
    residual_v = snapshot["v"][np.ix_(ix, iy)] - float(snapshot["v"][i, j])
    axis.streamplot(x[ix], y[iy], residual_u.T, residual_v.T, density=1.15, color="0.35", linewidth=0.55, arrowsize=0.55)
    axis.plot(x0, y0, "x", color="black", markersize=11, markeredgewidth=2.0)
    axis.set(
        xlabel="x/c", ylabel="y/c",
        title=f"Shock-ridge veto: d={candidate['shock_ridge_distance_cells']:.2f} cells",
    )
    axis.set_aspect("equal")
    fig.savefig(path, dpi=260, bbox_inches="tight")
    plt.close(fig)


def relative_l2(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(b - a) / max(float(np.linalg.norm(b)), 1.0e-300))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--aa-locked-config", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    cfg = json.loads((args.config or ROOT / "vortex_shock_ridge_aware_cmcd.json").read_text())
    locked = json.loads(args.aa_locked_config.read_text())
    if not locked.get("must_not_be_recalibrated_on_new_case"):
        parser.error("AA-ACB input is not locked for a new case")
    checkpoint = args.checkpoint.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = load_sibling("srcmcd_base", "run_vortex_acb_cmcd.py")
    artifact = load_sibling("srcmcd_artifact", "run_vortex_artifact_aware_acb.py")
    reference = load_sibling("srcmcd_reference", "run_dart_stage5_raw_reference.py")
    frozen = dict(locked["base_physics_configuration"])
    selector = dict(locked["candidate_budget_configuration"])
    artifact_cfg = json.loads((ROOT / "vortex_artifact_aware_acb.json").read_text())

    dx = float(cfg["raster_spacing"])
    x = np.arange(float(cfg["analysis_xlim"][0]), float(cfg["analysis_xlim"][1]) + 0.5 * dx, dx)
    y = np.arange(float(cfg["analysis_ylim"][0]), float(cfg["analysis_ylim"][1]) + 0.5 * dx, dx)
    geometry_fluid = reference.geometry_fluid_mask(x, y)
    snapshots: list[dict] = []
    raw_sources: list[dict[str, np.ndarray]] = []
    per_snapshot: list[dict] = []
    feature_rows: list[dict] = []
    final_detections: list[dict] = []
    rejection_counter: Counter[str] = Counter()

    with zipfile.ZipFile(checkpoint) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f"corrupt checkpoint member: {bad}")
        status = json.loads(archive.read("status.json"))
        manifest = json.loads(archive.read("checkpoint_manifest.json"))
        members = list(cfg["restart_members"])
        absent = [name for name in members if name not in archive.namelist()]
        if absent:
            parser.error(f"checkpoint lacks restart members: {absent}")
        triangulation = None
        reference_coordinates = None
        for frame, member in enumerate(members):
            raw = read_su2_restart(archive, member, float(cfg["gamma"]))
            if not all(np.all(np.isfinite(values)) for values in raw.values()):
                raise RuntimeError(f"nonfinite SU2 values in {member}")
            coordinates = np.column_stack((raw["x"], raw["y"]))
            if triangulation is None:
                from scipy.spatial import Delaunay
                triangulation = Delaunay(coordinates)
                reference_coordinates = coordinates
            elif not np.array_equal(coordinates, reference_coordinates):
                raise RuntimeError("SU2 O-grid coordinates change between restart snapshots")
            native = derive_native_ogrid_fields(
                raw, int(cfg["radial_points"]), int(cfg["circumferential_points"])
            )
            fields = interpolate_native_fields(triangulation, native, x, y)
            finite = np.logical_and.reduce([np.isfinite(values) for values in fields.values()])
            if not np.all(finite[geometry_fluid]):
                raise RuntimeError("analysis window extends beyond the SU2 mesh")
            fields["u"][~geometry_fluid] = 0.0
            fields["v"][~geometry_fluid] = 0.0
            fields = finish_raster_fields(
                x, y, fields, geometry_fluid, artifact_cfg["gaussian_sigmas"], float(cfg["gamma"])
            )
            snapshot = {"x": x, "y": y, "fluid": geometry_fluid & finite, **fields}
            shock_mask, shock_distance = build_shock_ridge_mask(snapshot, cfg)
            snapshot["shock_ridge_mask"] = shock_mask
            snapshot["shock_ridge_distance"] = shock_distance
            raw_candidates, threshold = base.all_q_candidates(snapshot, frozen)
            physically_valid, _ = artifact.filter_candidates(raw_candidates, snapshot, artifact_cfg)
            original_selected, selection = base.select_adaptive(physically_valid, selector)
            audit_rows: list[dict] = []
            for rank, candidate in enumerate(original_selected, start=1):
                rings = [
                    ring_winding_features(snapshot, candidate, float(radius), int(cfg["winding_samples"]))
                    for radius in cfg["winding_radii_cells"]
                ]
                for row in rings:
                    row["pass"] = winding_pass(row, cfg)
                winding_support = sum(bool(row["pass"]) for row in rings)
                island = closed_q_island(snapshot, candidate, cfg)
                pressure = scale_adaptive_pressure_support(
                    pressure_core_support(snapshot, candidate, cfg), island, cfg
                )
                shock_distance_cells = float(shock_distance[int(candidate["grid_i"]), int(candidate["grid_j"])])
                pre_shock = bool(
                    island["pass"]
                    and winding_support >= int(cfg["minimum_winding_ring_support"])
                    and pressure["pass"]
                )
                accepted, reason = revised_decision(
                    island, winding_support, pressure, shock_distance_cells, cfg
                )
                row = {
                    **candidate, "rank": rank, "frame_index": frame,
                    "source_member": member, "q_island": island,
                    "winding_rings": rings, "winding_support": winding_support,
                    "pressure_core": pressure, "shock_ridge_distance_cells": shock_distance_cells,
                    "pre_shock_accepted": pre_shock, "accepted": accepted,
                    "rejection_reason": reason,
                }
                audit_rows.append(row)
            rescue_corroborated_opposite_sign_pairs(audit_rows, cfg)
            suppress_subordinate_same_sign_peaks(audit_rows, cfg)
            for row in audit_rows:
                island = row["q_island"]
                pressure = row["pressure_core"]
                winding_support = int(row["winding_support"])
                shock_distance_cells = float(row["shock_ridge_distance_cells"])
                reason = str(row["rejection_reason"])
                rejection_counter[reason] += int(not row["accepted"])
                feature_rows.append({
                    "frame_index": frame, "source_member": member, "rank": row["rank"],
                    "x": row["x"], "y": row["y"], "rotation_sign": row["sign"],
                    "q_score": row["score"], "q_island_closed": island["closed"],
                    "q_island_area_cells": island["area_cells"],
                    "q_island_aspect_ratio": island["aspect_ratio"],
                    "q_island_analysis_radius_cells": island["analysis_radius_cells"],
                    "winding_support": winding_support,
                    "pressure_ring_support": pressure["ring_support"],
                    "pressure_minimum_offset_cells": pressure["offset_cells"],
                    "shock_ridge_distance_cells": shock_distance_cells,
                    "pre_shock_accepted": row["pre_shock_accepted"],
                    "accepted": row["accepted"],
                    "rejection_reason": reason,
                })
                if row["accepted"]:
                    final_detections.append({
                        "frame_index": frame, "source_member": member,
                        "rank": len(final_detections) + 1, "x": row["x"], "y": row["y"],
                        "rotation_sign": row["sign"], "q_score": row["score"],
                    })
            step_match = re.search(r"_(\d+)\.csv$", member)
            per_snapshot.append({
                "frame_index": frame,
                "source_step": int(step_match.group(1)) if step_match else frame,
                "source_member": member,
                "raw_q_candidates": len(raw_candidates),
                "artifact_valid_candidates": len(physically_valid),
                "frozen_aa_selected": len(original_selected),
                "closed_loop_pressure_candidates": sum(bool(row["pre_shock_accepted"]) for row in audit_rows),
                "shock_ridge_rejections": sum(row["rejection_reason"] == "thermodynamic_shock_ridge_proximity" for row in audit_rows),
                "final_detections": sum(bool(row["accepted"]) for row in audit_rows),
                "robust_q_threshold": threshold["robust_q_threshold"],
                "selection_reason": selection["selection_reason"],
            })
            snapshots.append({**snapshot, "original_selected": original_selected, "audit": audit_rows})
            raw_sources.append(raw)

    for index, snapshot in enumerate(snapshots):
        draw_physical(
            output / f"shock_ridge_aware_physical_{index:04d}.png",
            snapshot, snapshot["original_selected"], snapshot["audit"],
            snapshot["shock_ridge_mask"], cfg,
        )
    draw_shock_zoom(
        output / "shock_ridge_aware_shock_bead_zoom.png",
        snapshots[-1], snapshots[-1]["audit"], snapshots[-1]["shock_ridge_mask"],
    )

    write_csv(output / "shock_ridge_aware_candidate_audit.csv", feature_rows, list(feature_rows[0]))
    if final_detections:
        write_csv(output / "shock_ridge_aware_detections.csv", final_detections, list(final_detections[0]))
    else:
        write_csv(
            output / "shock_ridge_aware_detections.csv", [],
            ["frame_index", "source_member", "rank", "x", "y", "rotation_sign", "q_score"],
        )
    write_csv(output / "shock_ridge_aware_per_snapshot.csv", per_snapshot, list(per_snapshot[0]))

    temporal = {}
    if len(raw_sources) >= 2:
        temporal = {
            name: {"relative_l2_change": relative_l2(raw_sources[-2][name], raw_sources[-1][name])}
            for name in ("rho", "u", "v", "pressure")
        }
        first = {(row["x"], row["y"], row["sign"]) for row in snapshots[-2]["original_selected"]}
        last = {(row["x"], row["y"], row["sign"]) for row in snapshots[-1]["original_selected"]}
        temporal["frozen_aa_exact_identity_overlap"] = len(first & last)
        temporal["frozen_aa_first_count"] = len(first)
        temporal["frozen_aa_last_count"] = len(last)

    gates = {
        "checkpoint_integrity": "pass",
        "finite_raw_fields": "pass",
        "native_ogrid_differentiation": "pass",
        "frozen_aa_configuration": "pass",
        "shock_ridge_veto_exercised": "pass" if rejection_counter["thermodynamic_shock_ridge_proximity"] > 0 else "fail",
        "independent_cross_case_validation": "not_run",
        "publication_claim": "fail",
    }
    report = {
        "schema_version": 1,
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_name": cfg["method_name"],
        "case_id": cfg["case_id"],
        "checkpoint": {
            "path": str(checkpoint), "sha256": file_sha256(checkpoint),
            "status": status, "manifest": manifest,
        },
        "protocol": {
            "alpha40_role": "unlabelled development diagnostic; never a zero-vortex negative control or independent validation",
            "frozen_aa_configuration_recalibrated": False,
            "shock_ridge_thresholds_predeclared": True,
            "human_labels_used_for_runtime_decisions": False,
            "native_su2_ogrid_gradients_before_cartesian_interpolation": True,
        },
        "configuration": cfg,
        "per_snapshot": per_snapshot,
        "temporal_alias_audit": temporal,
        "rejections": dict(rejection_counter),
        "final_detection_count": len(final_detections),
        "gates": gates,
        "claim_gate": "su2_alpha40_unlabelled_development_diagnostic_only",
        "limitations": [
            "The SU2 checkpoint metadata is CHECKPOINTED/NOT_QUALIFIED and is not a standalone CFD validation result.",
            "Only two adjacent, nearly identical restart states are present; exact persistence is not temporal validation.",
            "The alpha-40 SU2 case informed shock-ridge feature design and is permanently excluded from blind validation.",
            "A conservative shock-ridge veto may reject a genuine vortex interacting directly with a shock.",
            "Publication validation requires frozen predictions on independently annotated, time-resolved cross-case data.",
        ],
    }
    (output / "shock_ridge_aware_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("SHOCK_RIDGE_AWARE_STATUS=completed")
    print(f"SHOCK_RIDGE_AWARE_FINAL_DETECTIONS={len(final_detections)}")
    print(f"SHOCK_RIDGE_AWARE_SHOCK_REJECTIONS={rejection_counter['thermodynamic_shock_ridge_proximity']}")
    print(f"SHOCK_RIDGE_AWARE_CLAIM_GATE={report['claim_gate']}")
    print(f"SHOCK_RIDGE_AWARE_REPORT={output / 'shock_ridge_aware_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
