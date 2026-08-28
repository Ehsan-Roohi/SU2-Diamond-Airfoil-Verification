#!/usr/bin/env python3
"""Extract article diagnostics from the completed MFC A40 HLL archive.

The solver's ``ib_state`` records are preferred when they contain finite
loads.  Some fixed-STL runs from MFC commit 0c9a1d4 contain NaN load slots;
for those archives this program reconstructs the same discrete volume force
used by MFC (negative pressure gradient plus divergence of viscous stress)
from every saved primitive field.  The chosen source and its validation state
are recorded in every summary so reconstructed loads cannot be mistaken for
native solver output.

Shock position means the bow-shock stand-off along the incoming freestream ray
from the leading edge.  Shock angle is the local ridge-tangent angle relative
to the freestream near that ray.  Both definitions are solver-independent and
can therefore be reused for SU2 and Nektar++ comparisons.
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import math
import os
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mfc-a40-article-matplotlib")

import numpy as np


AIRFOIL_X = np.array([0.0, 0.5, 1.0, 0.5, 0.0])
AIRFOIL_Y = np.array([0.0, 0.0702704174, 0.0, -0.0702704174, 0.0])
RECORD_WIDTH = 20


@dataclass(frozen=True)
class FlowReference:
    alpha_deg: float
    rho_inf: float
    u_inf: float
    chord: float
    reynolds: float

    @property
    def alpha(self) -> float:
        return math.radians(self.alpha_deg)

    @property
    def q_inf(self) -> float:
        return 0.5 * self.rho_inf * self.u_inf**2

    @property
    def mu(self) -> float:
        return self.rho_inf * self.u_inf * self.chord / self.reynolds


def finite_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def step_from_name(path: Path) -> int:
    match = re.search(r"(\d+)(?=\.dat$)", path.name)
    if match is None:
        raise ValueError(f"Cannot parse a time step from {path}")
    return int(match.group(1))


def nearest_indices(coords: np.ndarray, targets: np.ndarray) -> np.ndarray:
    right = np.searchsorted(coords, targets, side="left")
    right = np.clip(right, 0, len(coords) - 1)
    left = np.clip(right - 1, 0, len(coords) - 1)
    use_left = np.abs(targets - coords[left]) <= np.abs(coords[right] - targets)
    return np.where(use_left, left, right)


def derivative4(values: np.ndarray, spacing: float, axis: int) -> np.ndarray:
    """Fourth-order centered derivative with second-order boundary closure."""

    if values.shape[axis] < 5:
        return np.gradient(values, spacing, axis=axis, edge_order=2)
    result = np.gradient(values, spacing, axis=axis, edge_order=2)
    source = np.moveaxis(values, axis, 0)
    target = np.moveaxis(result, axis, 0)
    target[2:-2] = (
        source[:-4] - 8.0 * source[1:-3] + 8.0 * source[3:-1] - source[4:]
    ) / (12.0 * spacing)
    return result


def geometry_fluid_mask(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    pad = 3.0 * max(abs(dx), abs(dy))
    xx = x[:, None]
    yy = y[None, :]
    clipped = np.clip(xx, 0.0, 1.0)
    half_height = 0.0702704174 * (1.0 - np.abs(2.0 * clipped - 1.0))
    guarded_body = (
        (xx >= -pad)
        & (xx <= 1.0 + pad)
        & (np.abs(yy) <= half_height + pad)
    )
    return ~guarded_body


def geometry_ib_markers(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Reconstruct the fixed two-triangle STL occupancy at cell centers."""

    xx = x[:, None]
    yy = y[None, :]
    clipped = np.clip(xx, 0.0, 1.0)
    half_height = 0.0702704174 * (1.0 - np.abs(2.0 * clipped - 1.0))
    return ((xx >= 0.0) & (xx <= 1.0) & (np.abs(yy) <= half_height)).astype(np.int8)


def force_coefficients(
    force_x: float,
    force_y: float,
    ref: FlowReference,
) -> tuple[float, float]:
    drag = force_x * math.cos(ref.alpha) + force_y * math.sin(ref.alpha)
    lift = -force_x * math.sin(ref.alpha) + force_y * math.cos(ref.alpha)
    scale = ref.q_inf * ref.chord
    return drag / scale, lift / scale


def reconstruct_volume_force(
    x: np.ndarray,
    y: np.ndarray,
    rho: np.ndarray,
    pressure: np.ndarray,
    vel_x: np.ndarray,
    vel_y: np.ndarray,
    markers: np.ndarray,
    ref: FlowReference,
) -> dict[str, float]:
    """Reproduce MFC's fixed-IB volume integration on a uniform 2-D grid."""

    del rho  # Constant single-fluid dynamic viscosity is used by this case.
    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    if not np.allclose(np.diff(x), dx, rtol=1.0e-8, atol=1.0e-12):
        raise RuntimeError("Force reconstruction requires a uniform x grid")
    if not np.allclose(np.diff(y), dy, rtol=1.0e-8, atol=1.0e-12):
        raise RuntimeError("Force reconstruction requires a uniform y grid")
    solid = np.asarray(markers) != 0
    if not np.any(solid):
        raise RuntimeError("ib_markers contains no immersed-boundary cells")

    dp_dx = derivative4(pressure, dx, axis=0)
    dp_dy = derivative4(pressure, dy, axis=1)
    du_dx = derivative4(vel_x, dx, axis=0)
    du_dy = derivative4(vel_x, dy, axis=1)
    dv_dx = derivative4(vel_y, dx, axis=0)
    dv_dy = derivative4(vel_y, dy, axis=1)
    divergence = du_dx + dv_dy
    mu = ref.mu
    tau_xx = mu * (2.0 * du_dx - (2.0 / 3.0) * divergence)
    tau_xy = mu * (du_dy + dv_dx)
    tau_yy = mu * (2.0 * dv_dy - (2.0 / 3.0) * divergence)
    visc_x = derivative4(tau_xx, dx, axis=0) + derivative4(tau_xy, dy, axis=1)
    visc_y = derivative4(tau_xy, dx, axis=0) + derivative4(tau_yy, dy, axis=1)

    cell_area = dx * dy
    pressure_fx = float(np.sum((-dp_dx)[solid]) * cell_area)
    pressure_fy = float(np.sum((-dp_dy)[solid]) * cell_area)
    viscous_fx = float(np.sum(visc_x[solid]) * cell_area)
    viscous_fy = float(np.sum(visc_y[solid]) * cell_area)
    total_fx = pressure_fx + viscous_fx
    total_fy = pressure_fy + viscous_fy
    cd, cl = force_coefficients(total_fx, total_fy, ref)
    cdp, clp = force_coefficients(pressure_fx, pressure_fy, ref)
    cdv, clv = force_coefficients(viscous_fx, viscous_fy, ref)
    return {
        "force_x": total_fx,
        "force_y": total_fy,
        "force_x_pressure": pressure_fx,
        "force_y_pressure": pressure_fy,
        "force_x_viscous": viscous_fx,
        "force_y_viscous": viscous_fy,
        "CD": cd,
        "CL": cl,
        "CD_pressure": cdp,
        "CL_pressure": clp,
        "CD_viscous": cdv,
        "CL_viscous": clv,
    }


def bilinear_sample(
    x: np.ndarray,
    y: np.ndarray,
    field: np.ndarray,
    sample_x: np.ndarray,
    sample_y: np.ndarray,
) -> np.ndarray:
    ix = np.searchsorted(x, sample_x, side="right") - 1
    iy = np.searchsorted(y, sample_y, side="right") - 1
    ix = np.clip(ix, 0, len(x) - 2)
    iy = np.clip(iy, 0, len(y) - 2)
    x0 = x[ix]
    y0 = y[iy]
    wx = (sample_x - x0) / (x[ix + 1] - x0)
    wy = (sample_y - y0) / (y[iy + 1] - y0)
    return (
        (1.0 - wx) * (1.0 - wy) * field[ix, iy]
        + wx * (1.0 - wy) * field[ix + 1, iy]
        + (1.0 - wx) * wy * field[ix, iy + 1]
        + wx * wy * field[ix + 1, iy + 1]
    )


def reconstruct_surface_force(
    x: np.ndarray,
    y: np.ndarray,
    pressure: np.ndarray,
    vel_x: np.ndarray,
    vel_y: np.ndarray,
    ref: FlowReference,
    samples_per_face: int = 400,
) -> dict[str, float]:
    """Integrate pressure and viscous traction just outside the exact STL."""

    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    du_dx = derivative4(vel_x, dx, axis=0)
    du_dy = derivative4(vel_x, dy, axis=1)
    dv_dx = derivative4(vel_y, dx, axis=0)
    dv_dy = derivative4(vel_y, dy, axis=1)
    divergence = du_dx + dv_dy
    tau_xx = ref.mu * (2.0 * du_dx - (2.0 / 3.0) * divergence)
    tau_xy = ref.mu * (du_dy + dv_dx)
    tau_yy = ref.mu * (2.0 * dv_dy - (2.0 / 3.0) * divergence)

    pressure_fx = pressure_fy = viscous_fx = viscous_fy = 0.0
    grid_scale = max(abs(dx), abs(dy))
    offset_near = 2.5 * grid_scale
    offset_far = 4.5 * grid_scale
    vertices = np.column_stack([AIRFOIL_X[:-1], AIRFOIL_Y[:-1]])
    for face in range(len(vertices)):
        beginning = vertices[face]
        end = vertices[(face + 1) % len(vertices)]
        tangent = end - beginning
        length = float(np.linalg.norm(tangent))
        unit_tangent = tangent / length
        # The vertex order is clockwise, so the left normal points outward.
        normal = np.array([-unit_tangent[1], unit_tangent[0]])
        fraction = (np.arange(samples_per_face, dtype=float) + 0.5) / samples_per_face
        wall_points = beginning[None, :] + fraction[:, None] * tangent[None, :]
        near = wall_points + offset_near * normal[None, :]
        far = wall_points + offset_far * normal[None, :]

        def wall_extrapolation(field: np.ndarray) -> np.ndarray:
            near_value = bilinear_sample(x, y, field, near[:, 0], near[:, 1])
            far_value = bilinear_sample(x, y, field, far[:, 0], far[:, 1])
            return (
                offset_far * near_value - offset_near * far_value
            ) / (offset_far - offset_near)

        p = wall_extrapolation(pressure)
        txx = wall_extrapolation(tau_xx)
        txy = wall_extrapolation(tau_xy)
        tyy = wall_extrapolation(tau_yy)
        ds = length / samples_per_face
        pressure_fx += float(np.sum(-p * normal[0]) * ds)
        pressure_fy += float(np.sum(-p * normal[1]) * ds)
        viscous_fx += float(np.sum(txx * normal[0] + txy * normal[1]) * ds)
        viscous_fy += float(np.sum(txy * normal[0] + tyy * normal[1]) * ds)

    total_fx = pressure_fx + viscous_fx
    total_fy = pressure_fy + viscous_fy
    cd, cl = force_coefficients(total_fx, total_fy, ref)
    cdp, clp = force_coefficients(pressure_fx, pressure_fy, ref)
    cdv, clv = force_coefficients(viscous_fx, viscous_fy, ref)
    return {
        "force_x": total_fx,
        "force_y": total_fy,
        "force_x_pressure": pressure_fx,
        "force_y_pressure": pressure_fy,
        "force_x_viscous": viscous_fx,
        "force_y_viscous": viscous_fy,
        "CD": cd,
        "CL": cl,
        "CD_pressure": cdp,
        "CL_pressure": clp,
        "CD_viscous": cdv,
        "CL_viscous": clv,
    }


def density_gradient(
    x: np.ndarray, y: np.ndarray, rho: np.ndarray
) -> np.ndarray:
    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    return np.hypot(
        derivative4(rho, dx, axis=0), derivative4(rho, dy, axis=1)
    )


def bow_shock_metric(
    x: np.ndarray,
    y: np.ndarray,
    grad_rho: np.ndarray,
    fluid: np.ndarray,
    alpha_deg: float,
    n_limit: float = 0.35,
    s_min: float = -1.25,
    s_max: float = -0.02,
) -> dict[str, Any]:
    """Trace the detached bow shock in freestream coordinates.

    ``s`` is aligned with the freestream and ``n`` is its counter-clockwise
    normal.  For each cross-stream ray, the strongest upstream density ridge
    is selected and a weighted line ``s = a + b*n`` is fitted near ``n=0``.
    """

    alpha = math.radians(alpha_deg)
    n_values = np.linspace(-n_limit, n_limit, 81)
    s_values = np.linspace(s_min, s_max, 900)
    ridge_s = np.full(n_values.size, np.nan)
    ridge_strength = np.full(n_values.size, np.nan)

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
        ix = nearest_indices(x, x_ray[inside])
        iy = nearest_indices(y, y_ray[inside])
        signal = grad_rho[ix, iy]
        valid = np.asarray(fluid[ix, iy], dtype=bool) & np.isfinite(signal)
        if not np.any(valid):
            continue
        candidates = np.flatnonzero(valid)
        local_peak = int(candidates[np.argmax(signal[valid])])
        original = np.flatnonzero(inside)[local_peak]
        ridge_s[index] = s_values[original]
        ridge_strength[index] = signal[local_peak]

    finite_strength = ridge_strength[np.isfinite(ridge_strength)]
    if finite_strength.size < 12:
        return {
            "status": "NOT_DETECTED",
            "n": n_values,
            "s": ridge_s,
            "strength": ridge_strength,
        }
    floor = max(
        float(np.percentile(finite_strength, 30.0)),
        0.04 * float(np.max(finite_strength)),
    )
    accepted = (
        np.isfinite(ridge_s)
        & np.isfinite(ridge_strength)
        & (ridge_strength >= floor)
        & (np.abs(n_values) <= 0.24)
    )
    if np.count_nonzero(accepted) < 10:
        return {
            "status": "NOT_DETECTED",
            "n": n_values,
            "s": ridge_s,
            "strength": ridge_strength,
            "accepted": accepted,
        }

    weights = ridge_strength[accepted]
    design = np.column_stack(
        [np.ones(np.count_nonzero(accepted)), n_values[accepted]]
    )
    weighted_design = design * np.sqrt(weights)[:, None]
    weighted_target = ridge_s[accepted] * np.sqrt(weights)
    intercept, slope = np.linalg.lstsq(
        weighted_design, weighted_target, rcond=None
    )[0]
    fitted = intercept + slope * n_values[accepted]
    fit_rms = float(
        np.sqrt(np.average((ridge_s[accepted] - fitted) ** 2, weights=weights))
    )

    tangent_x = slope * math.cos(alpha) - math.sin(alpha)
    tangent_y = slope * math.sin(alpha) + math.cos(alpha)
    chord_angle = math.degrees(math.atan2(tangent_y, tangent_x)) % 180.0
    delta = abs(chord_angle - (alpha_deg % 180.0))
    shock_angle = min(delta, 180.0 - delta)
    x_ridge = ridge_s * math.cos(alpha) - n_values * math.sin(alpha)
    y_ridge = ridge_s * math.sin(alpha) + n_values * math.cos(alpha)
    return {
        "status": "PASS",
        "n": n_values,
        "s": ridge_s,
        "x": x_ridge,
        "y": y_ridge,
        "strength": ridge_strength,
        "accepted": accepted,
        "strength_floor": floor,
        "fit_intercept_s_over_c": float(intercept),
        "fit_slope_ds_dn": float(slope),
        "fit_rms_over_c": fit_rms,
        "stand_off_over_c": float(-intercept),
        "ridge_x_at_center_over_c": float(intercept * math.cos(alpha)),
        "ridge_y_at_center_over_c": float(intercept * math.sin(alpha)),
        "shock_tangent_angle_from_chord_deg": float(chord_angle),
        "shock_angle_to_freestream_deg": float(shock_angle),
    }


def correlated_statistics(values: np.ndarray) -> dict[str, float | int]:
    values = np.asarray(values, dtype=float)
    if values.size == 0 or not np.isfinite(values).all():
        raise RuntimeError("Statistics require a finite nonempty series")
    mean = float(np.mean(values))
    fluctuation = values - mean
    rms = float(np.sqrt(np.mean(fluctuation**2)))
    n = int(values.size)
    tau = 1.0
    if n >= 4 and rms > 0.0:
        variance = float(np.mean(fluctuation**2))
        positive_sum = 0.0
        for lag in range(1, max(2, min(n // 3, 2000))):
            correlation = float(
                np.mean(fluctuation[:-lag] * fluctuation[lag:]) / variance
            )
            if correlation <= 0.0:
                break
            positive_sum += correlation
        tau = max(1.0, 1.0 + 2.0 * positive_sum)
    n_eff = max(1.0, n / tau)
    return {
        "samples": n,
        "mean": mean,
        "rms_fluctuation": rms,
        "temporal_std": float(np.std(values, ddof=1)) if n > 1 else 0.0,
        "tau_int_samples": float(tau),
        "n_eff": float(n_eff),
        "ci95_mean": float(1.96 * rms / math.sqrt(n_eff)),
        "minimum": float(np.min(values)),
        "maximum": float(np.max(values)),
    }


def spectral_metrics(
    time: np.ndarray,
    values: np.ndarray,
    ref: FlowReference,
) -> tuple[dict[str, Any], np.ndarray, np.ndarray]:
    time = np.asarray(time, dtype=float)
    values = np.asarray(values, dtype=float)
    if len(time) < 16:
        return {"status": "INSUFFICIENT_SAMPLES", "samples": len(time)}, np.array([]), np.array([])
    spacing = float(np.median(np.diff(time)))
    if spacing <= 0.0 or not np.allclose(np.diff(time), spacing, rtol=1.0e-6, atol=1.0e-10):
        return {"status": "NONUNIFORM_TIME_BASE", "samples": len(time)}, np.array([]), np.array([])

    centered_time = time - float(np.mean(time))
    trend = np.polyfit(centered_time, values, 1)
    signal = values - np.polyval(trend, centered_time)
    window = np.hanning(len(signal))
    nfft = max(4096, 2 ** math.ceil(math.log2(len(signal) * 16)))
    transform = np.fft.rfft(signal * window, n=nfft)
    frequency = np.fft.rfftfreq(nfft, d=spacing)
    psd = (np.abs(transform) ** 2) / max(float(np.sum(window**2)), 1.0e-30)
    duration = float(time[-1] - time[0])
    true_resolution = 1.0 / duration if duration > 0.0 else float("inf")
    band = (
        (frequency >= true_resolution)
        & (frequency <= 0.45 / spacing)
        & np.isfinite(psd)
    )
    if not np.any(band):
        return {"status": "NO_RESOLVED_FREQUENCY_BAND"}, frequency, psd
    band_indices = np.flatnonzero(band)
    peak_index = int(band_indices[np.argmax(psd[band])])
    peak_frequency = float(frequency[peak_index])
    peak_power = float(psd[peak_index])
    background = float(np.median(psd[band]))
    prominence = peak_power / max(background, 1.0e-30)

    autocorrelation = np.correlate(signal, signal, mode="full")[len(signal) - 1 :]
    if autocorrelation[0] > 0.0:
        autocorrelation = autocorrelation / autocorrelation[0]
    acf_peak_lag: int | None = None
    minimum_lag = max(2, int(round(spacing and 1.0 / (0.45 / spacing) / spacing)))
    for lag in range(minimum_lag, len(autocorrelation) - 1):
        if (
            autocorrelation[lag] > autocorrelation[lag - 1]
            and autocorrelation[lag] >= autocorrelation[lag + 1]
            and autocorrelation[lag] > 0.05
        ):
            acf_peak_lag = lag
            break
    acf_frequency = (
        1.0 / (acf_peak_lag * spacing) if acf_peak_lag is not None else None
    )
    consistency = (
        abs(acf_frequency - peak_frequency) / peak_frequency
        if acf_frequency is not None and peak_frequency > 0.0
        else None
    )
    cycles = peak_frequency * duration
    quality_checks = {
        "at_least_32_samples": len(time) >= 32,
        "at_least_5_cycles": cycles >= 5.0,
        "peak_to_median_power_at_least_5": prominence >= 5.0,
        "acf_frequency_agrees_with_fft_within_20_percent": (
            consistency is not None and consistency <= 0.20
        ),
    }
    status = "ARTICLE_READY" if all(quality_checks.values()) else "PRELIMINARY_SHORT_RECORD"
    metrics = {
        "status": status,
        "samples": int(len(time)),
        "window": [float(time[0]), float(time[-1])],
        "sample_dt": spacing,
        "record_duration": duration,
        "true_frequency_resolution": true_resolution,
        "dominant_frequency": peak_frequency,
        "strouhal": peak_frequency * ref.chord / ref.u_inf,
        "cycles_in_window": cycles,
        "peak_to_median_power": prominence,
        "acf_frequency": acf_frequency,
        "fft_acf_relative_difference": consistency,
        "quality_checks": quality_checks,
        "method": "linearly detrended Hann periodogram; ACF peak cross-check",
        "frequency_units": "inverse MFC nondimensional time",
        "strouhal_definition": "St = f*c/U_inf",
    }
    return metrics, frequency, psd


def read_ib_state_history(case_dir: Path, ref: FlowReference) -> list[dict[str, float | int]]:
    rows: list[dict[str, float | int]] = []
    for path in sorted((case_dir / "restart_data").glob("ib_state_*.dat"), key=step_from_name):
        payload = path.read_bytes()
        if len(payload) < RECORD_WIDTH * 8 or len(payload) % (RECORD_WIDTH * 8):
            continue
        record = struct.unpack(f"={RECORD_WIDTH}d", payload[: RECORD_WIDTH * 8])
        time, force_x, force_y = record[:3]
        cd, cl = force_coefficients(force_x, force_y, ref)
        rows.append(
            {
                "step": step_from_name(path),
                "time": time,
                "force_x": force_x,
                "force_y": force_y,
                "CD": cd,
                "CL": cl,
            }
        )
    return rows


def ib_history_is_usable(rows: list[dict[str, float | int]], expected_steps: list[int]) -> bool:
    if len(rows) != len(expected_steps):
        return False
    if [int(row["step"]) for row in rows] != expected_steps:
        return False
    return all(
        math.isfinite(float(row[key]))
        for row in rows
        for key in ("time", "force_x", "force_y", "CD", "CL")
    )


def snapshot_rows(
    case_dir: Path,
    mfc_root: Path,
    steps: list[int],
    dt: float,
    ref: FlowReference,
    direct_rows: list[dict[str, float | int]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    sys.path.insert(0, str(mfc_root / "toolchain"))
    try:
        from mfc.viz.reader import assemble
    except ImportError as exc:
        raise RuntimeError(f"Cannot import MFC field reader from {mfc_root}: {exc}") from exc

    direct_by_step = {int(row["step"]): row for row in direct_rows}
    force_rows: list[dict[str, Any]] = []
    shock_rows: list[dict[str, Any]] = []
    validation_errors: list[float] = []
    volume_surface_errors: list[float] = []
    reference_grid: tuple[int, int] | None = None
    final_plot: dict[str, Any] = {}

    for index, step in enumerate(steps, start=1):
        print(f"FIELD {index}/{len(steps)} step={step}", flush=True)
        assembled = assemble(str(case_dir), step, fmt="binary")
        required = {"rho", "pres", "vel1", "vel2"}
        missing = sorted(required - set(assembled.variables))
        if missing:
            raise RuntimeError(f"Step {step} lacks fields required for article diagnostics: {missing}")
        x = np.asarray(assembled.x_cc, dtype=float)
        y = np.asarray(assembled.y_cc, dtype=float)
        shape = (x.size, y.size)
        if reference_grid is None:
            reference_grid = shape
        elif shape != reference_grid:
            raise RuntimeError(f"Grid shape changed from {reference_grid} to {shape} at step {step}")
        rho = np.asarray(assembled.variables["rho"], dtype=float)
        pressure = np.asarray(assembled.variables["pres"], dtype=float)
        vel_x = np.asarray(assembled.variables["vel1"], dtype=float)
        vel_y = np.asarray(assembled.variables["vel2"], dtype=float)
        if "ib_markers" in assembled.variables:
            markers = np.asarray(assembled.variables["ib_markers"])
            marker_source = "MFC_POST_PROCESS_IB_MARKERS"
            fluid = markers == 0
        else:
            markers = geometry_ib_markers(x, y)
            marker_source = "RECONSTRUCTED_FROM_EXACT_TWO_TRIANGLE_STL"
            fluid = geometry_fluid_mask(x, y)
        if not all(np.isfinite(field).all() for field in (rho, pressure, vel_x, vel_y)):
            raise RuntimeError(f"Non-finite primitive field at step {step}")
        volume_reconstructed = reconstruct_volume_force(
            x, y, rho, pressure, vel_x, vel_y, markers, ref
        )
        reconstructed = reconstruct_surface_force(
            x, y, pressure, vel_x, vel_y, ref
        )
        comparison_scale = max(
            math.hypot(reconstructed["force_x"], reconstructed["force_y"]),
            ref.q_inf * ref.chord * 1.0e-12,
        )
        volume_surface_errors.append(
            math.hypot(
                reconstructed["force_x"] - volume_reconstructed["force_x"],
                reconstructed["force_y"] - volume_reconstructed["force_y"],
            )
            / comparison_scale
        )
        force_row: dict[str, Any] = {
            "step": step,
            "time": step * dt,
            **reconstructed,
            **{
                f"volume_{key}": value
                for key, value in volume_reconstructed.items()
            },
        }
        direct = direct_by_step.get(step)
        if direct is not None and all(
            math.isfinite(float(direct[key])) for key in ("force_x", "force_y")
        ):
            force_row["direct_force_x"] = float(direct["force_x"])
            force_row["direct_force_y"] = float(direct["force_y"])
            scale = max(
                math.hypot(float(direct["force_x"]), float(direct["force_y"])),
                ref.q_inf * ref.chord * 1.0e-12,
            )
            validation_errors.append(
                math.hypot(
                    reconstructed["force_x"] - float(direct["force_x"]),
                    reconstructed["force_y"] - float(direct["force_y"]),
                )
                / scale
            )
        force_rows.append(force_row)

        grad = density_gradient(x, y, rho)
        shock = bow_shock_metric(x, y, grad, fluid, ref.alpha_deg)
        shock_rows.append(
            {
                "step": step,
                "time": step * dt,
                "status": shock["status"],
                "stand_off_over_c": finite_or_none(shock.get("stand_off_over_c")),
                "shock_angle_to_freestream_deg": finite_or_none(
                    shock.get("shock_angle_to_freestream_deg")
                ),
                "shock_tangent_angle_from_chord_deg": finite_or_none(
                    shock.get("shock_tangent_angle_from_chord_deg")
                ),
                "fit_rms_over_c": finite_or_none(shock.get("fit_rms_over_c")),
            }
        )
        if step == steps[-1]:
            final_plot = {
                "x": x.copy(),
                "y": y.copy(),
                "grad": grad.copy(),
                "fluid": fluid.copy(),
                "shock": shock,
            }
        del assembled, rho, pressure, vel_x, vel_y, markers, grad
        gc.collect()

    validation: dict[str, Any] = {
        "native_finite_samples": len(validation_errors),
        "relative_vector_error_mean": (
            float(np.mean(validation_errors)) if validation_errors else None
        ),
        "relative_vector_error_max": (
            float(np.max(validation_errors)) if validation_errors else None
        ),
        "status": (
            "PASS_NATIVE_CROSSCHECK"
            if validation_errors and max(validation_errors) <= 0.02
            else "NO_FINITE_NATIVE_LOADS_FOR_CROSSCHECK"
            if not validation_errors
            else "FIELD_RECONSTRUCTION_DIFFERS_FROM_NATIVE"
        ),
        "ib_marker_source": marker_source,
        "surface_traction_extrapolation_offset_cells": [2.5, 4.5],
        "surface_vs_volume_relative_vector_error_mean": float(
            np.mean(volume_surface_errors)
        ),
        "surface_vs_volume_relative_vector_error_max": float(
            np.max(volume_surface_errors)
        ),
    }
    return force_rows, shock_rows, {"validation": validation, "final_plot": final_plot}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"Cannot write empty table: {path}")
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_comparison(path: Path, expected_alpha: float) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    alpha = finite_or_none(payload.get("alpha_deg"))
    if alpha is not None and not math.isclose(alpha, expected_alpha, abs_tol=0.05):
        return {
            "method": payload.get("method", path.stem),
            "status": "ANGLE_MISMATCH",
            "alpha_deg": alpha,
            "notes": f"Expected alpha={expected_alpha:g} deg",
        }
    stats = payload.get("statistics", payload)
    force = payload.get("force_statistics", stats)
    spectrum = payload.get("shedding", payload.get("spectrum", {}))
    shock = payload.get("shock_statistics", payload.get("shock", {}))

    def nested_metric(container: Any, name: str, field: str = "mean") -> float | None:
        if not isinstance(container, dict):
            return None
        value = container.get(name)
        if isinstance(value, dict):
            return finite_or_none(value.get(field))
        return finite_or_none(value)

    return {
        "method": payload.get("method", path.stem),
        "status": payload.get("status", "IMPORTED"),
        "alpha_deg": alpha if alpha is not None else expected_alpha,
        "CL_mean": nested_metric(force, "CL"),
        "CL_rms": nested_metric(force, "CL", "rms_fluctuation"),
        "CD_mean": nested_metric(force, "CD"),
        "CD_rms": nested_metric(force, "CD", "rms_fluctuation"),
        "Strouhal": finite_or_none(
            spectrum.get("strouhal") if isinstance(spectrum, dict) else None
        ),
        "shock_stand_off_over_c": nested_metric(shock, "stand_off_over_c"),
        "shock_angle_to_freestream_deg": nested_metric(
            shock, "shock_angle_to_freestream_deg"
        ),
        "notes": f"Imported from {path}",
    }


def compact_column(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def find_column(fieldnames: Iterable[str], candidates: Iterable[str]) -> str | None:
    lookup = {compact_column(name): name for name in fieldnames}
    for candidate in candidates:
        key = compact_column(candidate)
        if key in lookup:
            return lookup[key]
    return None


def read_su2_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("%", 1)[0].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().upper()] = value.strip()
    return values


def su2_convective_time_scale(config: dict[str, str]) -> dict[str, float]:
    try:
        dt_seconds = float(config["TIME_STEP"])
        mach = float(config["MACH_NUMBER"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError("SU2 cfg must contain numeric TIME_STEP and MACH_NUMBER") from exc
    gamma = float(config.get("GAMMA_VALUE", 1.4))
    gas_constant = float(config.get("GAS_CONSTANT", 287.058))
    temperature = float(config.get("FREESTREAM_TEMPERATURE", 288.15))
    chord = float(config.get("REF_LENGTH", 1.0))
    velocity_raw = config.get("FREESTREAM_VELOCITY")
    if velocity_raw:
        components = [
            float(value)
            for value in re.findall(r"[-+0-9.eE]+", velocity_raw)
        ]
        speed = math.sqrt(sum(value * value for value in components))
    else:
        speed = mach * math.sqrt(gamma * gas_constant * temperature)
    if min(dt_seconds, speed, chord) <= 0.0:
        raise RuntimeError("SU2 time normalization contains a nonpositive value")
    return {
        "dt_seconds": dt_seconds,
        "speed": speed,
        "chord": chord,
        "convective_time_per_iteration": dt_seconds * speed / chord,
    }


def load_su2_history(
    history_path: Path,
    config_path: Path,
    expected_alpha: float,
    output: Path,
) -> dict[str, Any]:
    config = read_su2_config(config_path)
    alpha = finite_or_none(config.get("AOA"))
    if alpha is None or not math.isclose(alpha, expected_alpha, abs_tol=0.05):
        return {
            "method": "SU2 URANS-SST",
            "status": "ANGLE_MISMATCH",
            "alpha_deg": alpha,
            "notes": f"Expected alpha={expected_alpha:g} deg; cfg={config_path}",
        }
    normalization = su2_convective_time_scale(config)
    history_files = (
        sorted(history_path.rglob("history*.csv"))
        if history_path.is_dir()
        else [history_path]
    )
    if not history_files:
        raise RuntimeError(f"No SU2 history CSVs found under {history_path}")
    by_iteration: dict[int, tuple[float, float]] = {}
    for history_file in history_files:
        with history_file.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                continue
            iter_column = find_column(reader.fieldnames, ("TIME_ITER", "TIMEITER"))
            cl_column = find_column(reader.fieldnames, ("LIFT", "CL"))
            cd_column = find_column(reader.fieldnames, ("DRAG", "CD"))
            if not iter_column or not cl_column or not cd_column:
                continue
            for row in reader:
                try:
                    iteration = int(float(row[iter_column]))
                    cl = float(row[cl_column])
                    cd = float(row[cd_column])
                except (KeyError, TypeError, ValueError):
                    continue
                if all(math.isfinite(value) for value in (cl, cd)):
                    by_iteration[iteration] = (cl, cd)
    if len(by_iteration) < 16:
        raise RuntimeError(f"SU2 history has only {len(by_iteration)} physical steps")
    iterations = np.asarray(sorted(by_iteration), dtype=int)
    time = iterations.astype(float) * normalization["convective_time_per_iteration"]
    cl = np.asarray([by_iteration[int(i)][0] for i in iterations])
    cd = np.asarray([by_iteration[int(i)][1] for i in iterations])
    start_index = len(iterations) // 2
    selected_time = time[start_index:]
    selected_cl = cl[start_index:]
    selected_cd = cd[start_index:]
    su2_ref = FlowReference(expected_alpha, 1.0, 1.0, 1.0, 1.0)
    shedding, _, _ = spectral_metrics(selected_time, selected_cl, su2_ref)
    standardized = [
        {
            "time_iteration": int(iteration),
            "convective_time": float(t),
            "CL": float(lift),
            "CD": float(drag),
        }
        for iteration, t, lift, drag in zip(iterations, time, cl, cd)
    ]
    write_csv(output / "su2_standardized_history.csv", standardized)
    cl_stats = correlated_statistics(selected_cl)
    cd_stats = correlated_statistics(selected_cd)
    summary = {
        "method": "SU2 URANS-SST",
        "status": "IMPORTED_ALPHA_MATCHED",
        "alpha_deg": alpha,
        "CL_mean": cl_stats["mean"],
        "CL_rms": cl_stats["rms_fluctuation"],
        "CD_mean": cd_stats["mean"],
        "CD_rms": cd_stats["rms_fluctuation"],
        "Strouhal": shedding.get("strouhal"),
        "shock_stand_off_over_c": None,
        "shock_angle_to_freestream_deg": None,
        "notes": (
            f"last-half window; history={history_path}; cfg={config_path}; "
            f"files={len(history_files)}; frequency_status={shedding.get('status')}"
        ),
        "normalization": normalization,
        "force_statistics": {"CL": cl_stats, "CD": cd_stats},
        "shedding": shedding,
    }
    (output / "su2_standardized_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_named_path(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("comparison must be METHOD=/path/to/summary.json")
    name, raw_path = value.split("=", 1)
    if not name.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("comparison must be METHOD=/path/to/summary.json")
    return name.strip(), Path(raw_path).expanduser()


def save_plots(
    output: Path,
    force_rows: list[dict[str, Any]],
    shock_rows: list[dict[str, Any]],
    spectrum_frequency: np.ndarray,
    spectrum_psd: np.ndarray,
    final_plot: dict[str, Any],
    analysis_start: float,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    time = np.asarray([row["time"] for row in force_rows], dtype=float)
    fig, axes = plt.subplots(2, 1, figsize=(9.0, 6.7), sharex=True, constrained_layout=True)
    for ax, key, color in ((axes[0], "CL", "#2166ac"), (axes[1], "CD", "#b2182b")):
        values = np.asarray([row[key] for row in force_rows], dtype=float)
        ax.plot(time, values, color=color, lw=1.25)
        ax.axvspan(analysis_start, time[-1], color="#cccccc", alpha=0.25)
        ax.set_ylabel(rf"${key}$")
        ax.grid(alpha=0.25)
    axes[-1].set_xlabel("MFC nondimensional time")
    fig.suptitle("MFC HLL immersed-boundary force history")
    fig.savefig(output / "mfc_hll_force_history.png", dpi=240)
    plt.close(fig)

    if spectrum_frequency.size:
        fig, ax = plt.subplots(figsize=(8.2, 4.8), constrained_layout=True)
        positive = spectrum_frequency > 0.0
        ax.semilogy(spectrum_frequency[positive], spectrum_psd[positive], color="#542788")
        ax.set_xlim(0.0, min(10.0, float(np.max(spectrum_frequency))))
        ax.set_xlabel("frequency, inverse MFC time")
        ax.set_ylabel("Hann periodogram power")
        ax.set_title("Lift-coefficient shedding spectrum")
        ax.grid(alpha=0.25)
        fig.savefig(output / "mfc_hll_lift_spectrum.png", dpi=240)
        plt.close(fig)

    valid_shock = [
        row for row in shock_rows if row["stand_off_over_c"] is not None
    ]
    if valid_shock:
        stime = np.asarray([row["time"] for row in valid_shock])
        stand = np.asarray([row["stand_off_over_c"] for row in valid_shock])
        angle = np.asarray([row["shock_angle_to_freestream_deg"] for row in valid_shock])
        fig, axes = plt.subplots(2, 1, figsize=(8.5, 6.4), sharex=True, constrained_layout=True)
        axes[0].plot(stime, stand, color="#1b7837")
        axes[0].set_ylabel(r"stand-off $s/c$")
        axes[1].plot(stime, angle, color="#e66101")
        axes[1].set_ylabel(r"shock angle $\beta$ (deg)")
        axes[1].set_xlabel("MFC nondimensional time")
        for ax in axes:
            ax.grid(alpha=0.25)
        fig.suptitle("Leading-edge bow-shock history")
        fig.savefig(output / "mfc_hll_shock_history.png", dpi=240)
        plt.close(fig)

    if final_plot:
        x = final_plot["x"]
        y = final_plot["y"]
        grad = final_plot["grad"]
        fluid = final_plot["fluid"]
        shock = final_plot["shock"]
        xmask = (x >= -1.25) & (x <= 2.0)
        ymask = (y >= -1.5) & (y <= 2.0)
        xi = np.flatnonzero(xmask)[::2]
        yi = np.flatnonzero(ymask)[::2]
        field = np.where(fluid[np.ix_(xi, yi)], grad[np.ix_(xi, yi)], np.nan)
        hi = float(np.nanpercentile(field, 99.7))
        fig, ax = plt.subplots(figsize=(8.2, 7.1), constrained_layout=True)
        mesh = ax.pcolormesh(x[xi], y[yi], field.T, shading="auto", cmap="gray", vmin=0.0, vmax=hi)
        accepted = np.asarray(shock.get("accepted", []), dtype=bool)
        if accepted.size:
            ax.plot(np.asarray(shock["x"])[accepted], np.asarray(shock["y"])[accepted], color="#d73027", lw=1.5, label="fitted bow-shock ridge")
            ax.legend(loc="upper left")
        ax.fill(AIRFOIL_X, AIRFOIL_Y, color="black")
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(-1.25, 2.0)
        ax.set_ylim(-1.5, 2.0)
        ax.set_xlabel(r"$x/c$")
        ax.set_ylabel(r"$y/c$")
        ax.set_title("Final density-gradient field and measured bow-shock ridge")
        fig.colorbar(mesh, ax=ax, label=r"$|\nabla\rho|c/\rho_\infty$")
        fig.savefig(output / "mfc_hll_final_shock_fit.png", dpi=240)
        plt.close(fig)


def run(args: argparse.Namespace) -> dict[str, Any]:
    case_dir = args.case_dir.expanduser().resolve()
    mfc_root = args.mfc_root.expanduser().resolve()
    if not case_dir.is_dir():
        raise RuntimeError(f"Case directory does not exist: {case_dir}")
    if not (mfc_root / "toolchain" / "mfc" / "viz" / "reader.py").is_file():
        raise RuntimeError(f"MFC reader was not found under {mfc_root}")
    output = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else case_dir / "article_diagnostics"
    )
    output.mkdir(parents=True, exist_ok=True)
    ref = FlowReference(
        alpha_deg=args.alpha,
        rho_inf=args.rho_inf,
        u_inf=args.u_inf,
        chord=args.chord,
        reynolds=args.reynolds,
    )

    sys.path.insert(0, str(mfc_root / "toolchain"))
    from mfc.viz.reader import discover_timesteps

    steps = [int(step) for step in discover_timesteps(str(case_dir), "binary")]
    if len(steps) < 4:
        raise RuntimeError(f"Only {len(steps)} binary field snapshots were found")
    times = np.asarray(steps, dtype=float) * args.dt
    if args.analysis_start >= times[-1]:
        raise RuntimeError("--analysis-start must precede the final saved time")
    direct_rows = read_ib_state_history(case_dir, ref)
    direct_usable = ib_history_is_usable(direct_rows, steps)
    force_rows, shock_rows, field_info = snapshot_rows(
        case_dir, mfc_root, steps, args.dt, ref, direct_rows
    )
    if direct_usable:
        direct_by_step = {int(row["step"]): row for row in direct_rows}
        for row in force_rows:
            direct = direct_by_step[int(row["step"])]
            row["field_reconstructed_CD"] = row["CD"]
            row["field_reconstructed_CL"] = row["CL"]
            row["force_x"] = float(direct["force_x"])
            row["force_y"] = float(direct["force_y"])
            row["CD"] = float(direct["CD"])
            row["CL"] = float(direct["CL"])
        force_source = "NATIVE_IB_STATE"
    else:
        force_source = "FIELD_RECONSTRUCTED_SURFACE_TRACTION"

    write_csv(output / "mfc_hll_force_history.csv", force_rows)
    write_csv(output / "mfc_hll_shock_history.csv", shock_rows)
    active = [row for row in force_rows if float(row["time"]) >= args.analysis_start]
    if len(active) < 16:
        raise RuntimeError("Fewer than 16 force samples lie in the requested statistical window")
    force_statistics = {
        key: correlated_statistics(np.asarray([row[key] for row in active]))
        for key in ("CL", "CD", "CL_pressure", "CD_pressure", "CL_viscous", "CD_viscous")
    }
    active_time = np.asarray([row["time"] for row in active], dtype=float)
    active_cl = np.asarray([row["CL"] for row in active], dtype=float)
    shedding, frequency, psd = spectral_metrics(active_time, active_cl, ref)
    if frequency.size:
        spectrum_rows = [
            {"frequency": float(f), "strouhal": float(f * ref.chord / ref.u_inf), "power": float(p)}
            for f, p in zip(frequency, psd)
        ]
        write_csv(output / "mfc_hll_lift_spectrum.csv", spectrum_rows)

    active_shock = [
        row
        for row in shock_rows
        if float(row["time"]) >= args.analysis_start
        and row["stand_off_over_c"] is not None
        and row["shock_angle_to_freestream_deg"] is not None
    ]
    shock_statistics: dict[str, Any] = {
        "detected_samples": len(active_shock),
        "requested_samples": sum(float(row["time"]) >= args.analysis_start for row in shock_rows),
        "definition": {
            "position": "bow-shock stand-off along incoming freestream ray from leading edge",
            "angle": "local bow-shock ridge tangent angle relative to freestream",
        },
    }
    if active_shock:
        shock_statistics["stand_off_over_c"] = correlated_statistics(
            np.asarray([row["stand_off_over_c"] for row in active_shock])
        )
        shock_statistics["shock_angle_to_freestream_deg"] = correlated_statistics(
            np.asarray([row["shock_angle_to_freestream_deg"] for row in active_shock])
        )

    mfc_comparison = {
        "method": "MFC viscous ILES/no-model, WENO5-unmapped HLL",
        "status": (
            "ARTICLE_READY"
            if force_source == "NATIVE_IB_STATE"
            and shedding.get("status") == "ARTICLE_READY"
            else "PRELIMINARY"
        ),
        "alpha_deg": ref.alpha_deg,
        "CL_mean": force_statistics["CL"]["mean"],
        "CL_rms": force_statistics["CL"]["rms_fluctuation"],
        "CD_mean": force_statistics["CD"]["mean"],
        "CD_rms": force_statistics["CD"]["rms_fluctuation"],
        "Strouhal": shedding.get("strouhal"),
        "shock_stand_off_over_c": (
            shock_statistics.get("stand_off_over_c", {}).get("mean")
        ),
        "shock_angle_to_freestream_deg": (
            shock_statistics.get("shock_angle_to_freestream_deg", {}).get("mean")
        ),
        "notes": f"force_source={force_source}; shedding_status={shedding.get('status')}",
    }
    comparison_rows = [mfc_comparison]
    supplied_methods: set[str] = set()
    if args.su2_history is not None:
        if args.su2_config is None:
            raise RuntimeError("--su2-config is required with --su2-history")
        comparison_rows.append(
            load_su2_history(
                args.su2_history.expanduser().resolve(),
                args.su2_config.expanduser().resolve(),
                ref.alpha_deg,
                output,
            )
        )
        supplied_methods.add("su2")
    if args.nektar_summary is not None:
        imported_nektar = load_comparison(
            args.nektar_summary.expanduser().resolve(), ref.alpha_deg
        )
        imported_nektar["method"] = "Nektar++"
        comparison_rows.append(imported_nektar)
        supplied_methods.add("nektar")
    for name, path in args.comparison:
        imported = load_comparison(path.resolve(), ref.alpha_deg)
        imported["method"] = name
        supplied_methods.add(name.lower())
        comparison_rows.append(imported)
    if not any("su2" in name for name in supplied_methods):
        comparison_rows.append(
            {
                "method": "SU2",
                "status": "NOT_PROVIDED",
                "alpha_deg": ref.alpha_deg,
                "notes": "Pass --comparison 'SU2=/path/to/standardized-summary.json'",
            }
        )
    if not any("nektar" in name for name in supplied_methods):
        comparison_rows.append(
            {
                "method": "Nektar++",
                "status": "NOT_PROVIDED",
                "alpha_deg": ref.alpha_deg,
                "notes": "No alpha=40 Nektar++ production result is present in the repository",
            }
        )
    write_csv(output / "article_solver_comparison.csv", comparison_rows)

    summary = {
        "status": mfc_comparison["status"],
        "method": mfc_comparison["method"],
        "case_dir": str(case_dir),
        "alpha_deg": ref.alpha_deg,
        "Mach_inf": ref.u_inf,
        "Re_c": ref.reynolds,
        "dt": args.dt,
        "saved_steps": steps,
        "statistical_window": [args.analysis_start, float(times[-1])],
        "force_source": force_source,
        "force_source_assessment": (
            "NATIVE"
            if force_source == "NATIVE_IB_STATE"
            else "PROVISIONAL_UNTIL_VALIDATED_AGAINST_FINITE_NATIVE_MFC_LOADS"
        ),
        "field_force_validation": field_info["validation"],
        "force_statistics": force_statistics,
        "shedding": shedding,
        "shock_statistics": shock_statistics,
        "comparison": comparison_rows,
    }
    (output / "mfc_hll_article_metrics.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    summary_lines = [
        "MFC A40 HLL ARTICLE DIAGNOSTICS",
        "================================",
        f"status={summary['status']}",
        f"force_source={force_source}",
        f"window={args.analysis_start:g}..{times[-1]:g}",
        f"CL_mean={force_statistics['CL']['mean']:.10g}",
        f"CL_rms={force_statistics['CL']['rms_fluctuation']:.10g}",
        f"CD_mean={force_statistics['CD']['mean']:.10g}",
        f"CD_rms={force_statistics['CD']['rms_fluctuation']:.10g}",
        f"shedding_status={shedding.get('status')}",
        f"dominant_frequency={shedding.get('dominant_frequency')}",
        f"Strouhal={shedding.get('strouhal')}",
        f"shock_standoff_mean={mfc_comparison['shock_stand_off_over_c']}",
        f"shock_angle_mean_deg={mfc_comparison['shock_angle_to_freestream_deg']}",
        "SU2/Nektar++ rows are populated only from alpha-matched standardized summaries.",
    ]
    (output / "ARTICLE_SUMMARY.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    save_plots(
        output,
        force_rows,
        shock_rows,
        frequency,
        psd,
        field_info["final_plot"],
        args.analysis_start,
    )
    return summary


def self_test(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    ref = FlowReference(40.0, 1.0, 3.0, 1.0, 1.0e6)
    x = np.linspace(-1.5, 1.5, 301)
    y = np.linspace(-1.5, 1.5, 301)
    xx = x[:, None]
    yy = y[None, :]
    alpha = ref.alpha
    s = xx * math.cos(alpha) + yy * math.sin(alpha)
    n = -xx * math.sin(alpha) + yy * math.cos(alpha)
    shock_s = -0.24 + 0.12 * n
    rho = 1.0 + 0.8 / (1.0 + np.exp(-(s - shock_s) / 0.012))
    pressure = 1.0 + 0.4 * xx - 0.2 * yy
    vel_x = np.zeros_like(rho)
    vel_y = np.zeros_like(rho)
    markers = ((xx >= 0.0) & (xx <= 1.0) & (np.abs(yy) <= 0.07)).astype(int)
    force = reconstruct_volume_force(x, y, rho, pressure, vel_x, vel_y, markers, ref)
    surface_force = reconstruct_surface_force(x, y, pressure, vel_x, vel_y, ref)
    expected_fx = -0.4 * float(np.count_nonzero(markers)) * (x[1] - x[0]) * (y[1] - y[0])
    expected_fy = 0.2 * float(np.count_nonzero(markers)) * (x[1] - x[0]) * (y[1] - y[0])
    if not math.isclose(force["force_x"], expected_fx, rel_tol=2.0e-3):
        raise RuntimeError("Synthetic pressure-force x test failed")
    if not math.isclose(force["force_y"], expected_fy, rel_tol=2.0e-3):
        raise RuntimeError("Synthetic pressure-force y test failed")
    exact_area = 0.0702704174
    if not math.isclose(surface_force["force_x"], -0.4 * exact_area, rel_tol=3.0e-3):
        raise RuntimeError("Synthetic surface-pressure x test failed")
    if not math.isclose(surface_force["force_y"], 0.2 * exact_area, rel_tol=3.0e-3):
        raise RuntimeError("Synthetic surface-pressure y test failed")
    shock = bow_shock_metric(x, y, density_gradient(x, y, rho), np.ones_like(rho, dtype=bool), ref.alpha_deg)
    if shock["status"] != "PASS":
        raise RuntimeError("Synthetic shock was not detected")
    if not math.isclose(float(shock["stand_off_over_c"]), 0.24, abs_tol=0.025):
        raise RuntimeError(f"Synthetic shock stand-off failed: {shock['stand_off_over_c']}")
    time = np.arange(0.0, 12.0, 0.05)
    target_frequency = 1.25
    signal = 0.2 * np.sin(2.0 * np.pi * target_frequency * time)
    spectrum, _, _ = spectral_metrics(time, signal, ref)
    if not math.isclose(float(spectrum["dominant_frequency"]), target_frequency, rel_tol=0.02):
        raise RuntimeError("Synthetic spectrum test failed")
    report = {
        "status": "PASS",
        "force": force,
        "surface_force": surface_force,
        "shock_standoff": shock["stand_off_over_c"],
        "shock_angle": shock["shock_angle_to_freestream_deg"],
        "frequency": spectrum["dominant_frequency"],
        "Strouhal": spectrum["strouhal"],
    }
    (output / "self_test.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", nargs="?", type=Path)
    parser.add_argument("--mfc-root", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--dt", type=float, default=1.0 / 5400.0)
    parser.add_argument("--analysis-start", type=float, default=3.0)
    parser.add_argument("--alpha", type=float, default=40.0)
    parser.add_argument("--rho-inf", type=float, default=1.0)
    parser.add_argument("--u-inf", type=float, default=3.0)
    parser.add_argument("--chord", type=float, default=1.0)
    parser.add_argument("--reynolds", type=float, default=1.0e6)
    parser.add_argument("--su2-history", type=Path)
    parser.add_argument("--su2-config", type=Path)
    parser.add_argument("--nektar-summary", type=Path)
    parser.add_argument(
        "--comparison",
        action="append",
        type=parse_named_path,
        default=[],
        metavar="METHOD=SUMMARY.json",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(args.output_dir or Path("mfc_hll_article_self_test"))
        return 0
    if args.case_dir is None or args.mfc_root is None:
        parser.error("case_dir and --mfc-root are required unless --self-test is used")
    if min(args.dt, args.rho_inf, args.u_inf, args.chord, args.reynolds) <= 0.0:
        parser.error("dt and reference quantities must be positive")
    summary = run(args)
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    print(f"ARTICLE_DIAGNOSTICS={args.output_dir or args.case_dir / 'article_diagnostics'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
