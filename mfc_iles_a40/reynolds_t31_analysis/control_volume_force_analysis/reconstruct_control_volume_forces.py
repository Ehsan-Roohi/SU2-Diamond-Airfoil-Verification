#!/usr/bin/env python3
"""Reconstruct total airfoil loads from raw MFC fields by momentum balance.

The native ``ib_state`` force slots in the completed alpha=40 runs are NaN.
This post-processor therefore integrates the conservative momentum equation on
three fixed rectangular control volumes surrounding the airfoil.  It reads the
authoritative float64 ``restart_data/lustre_<step>.dat`` files; it neither runs
MFC nor estimates forces from the downsampled computer-vision tensors.

The reported load is the force of the fluid on the body,

    F_body = - d/dt integral_CV(rho*u) dA
             - integral_boundary_CV (rho*u*u + p*I - tau) . n ds.

Control-volume size spread is reported as method sensitivity.  Boundary-flux
terms are diagnostics and must not be described as a wall pressure/skin-friction
decomposition.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import struct
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mfc-control-volume-force-matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Polygon


METHOD = "CONTROL_VOLUME_MOMENTUM_BALANCE_FROM_RAW_FIELDS"
CLAIM_LEVEL = "RECONSTRUCTED_TREND_NOT_NATIVE_IB_LOAD"
NATIVE_FORCE_STATUS = "UNAVAILABLE_NAN"
ALPHA_DEG = 40.0
RHO_INF = 1.0
U_INF = 3.0
CHORD = 1.0
Q_INF = 0.5 * RHO_INF * U_INF**2
GAMMA = 1.4
P_INF = 1.0 / GAMMA
MFC_GAMMA_PARAMETER = 1.0 / (GAMMA - 1.0)
AIRFOIL_HALF_HEIGHT = 0.0702704174
NVAR = 5
FLOAT_DTYPE = np.dtype("<f8")
IB_RECORD_WIDTH = 20
IB_RECORD_BYTES = IB_RECORD_WIDTH * 8


@dataclass(frozen=True)
class GridSpec:
    name: str
    nx: int
    ny: int
    x_bounds: tuple[float, float] = (-5.0, 6.0)
    y_bounds: tuple[float, float] = (-5.0, 5.0)

    @property
    def field_bytes(self) -> int:
        return NVAR * self.nx * self.ny * FLOAT_DTYPE.itemsize


GRIDS = (
    GridSpec("f180", 1980, 1800),
    GridSpec("f270", 2970, 2700),
)
GRID_BY_BYTES = {grid.field_bytes: grid for grid in GRIDS}


@dataclass(frozen=True)
class ControlVolume:
    name: str
    xmin: float
    xmax: float
    ymin: float
    ymax: float

    @property
    def area(self) -> float:
        return (self.xmax - self.xmin) * (self.ymax - self.ymin)


CONTROL_VOLUMES = (
    ControlVolume("compact", -0.25, 1.25, -0.35, 0.35),
    ControlVolume("nominal", -0.50, 1.50, -0.60, 0.60),
    ControlVolume("wide", -0.75, 1.75, -0.85, 0.85),
)
CV_BY_NAME = {cv.name: cv for cv in CONTROL_VOLUMES}
NOMINAL_CV = "nominal"


@dataclass(frozen=True)
class CaseSpec:
    case: str
    reynolds: float
    grid: str
    stage: str
    case_dir: Path
    dt: float
    start_step: int
    stop_step: int
    stride: int
    window: str
    comparison_role: str


CASE_ORDER = (
    "re1e4_f180",
    "re1e4_f270",
    "re5e4_f180",
    "re1e5_f180",
    "re1e6_f270_mature",
)
CASE_LABELS = {
    "re1e4_f180": r"$Re_c=10^4$, f180",
    "re1e4_f270": r"$Re_c=10^4$, f270",
    "re5e4_f180": r"$Re_c=5\times10^4$, f180",
    "re1e5_f180": r"$Re_c=10^5$, f180",
    "re1e6_f270_mature": r"$Re_c=10^6$, f270, $t=26$--31",
}
COLORS = {
    "re1e4_f180": "#277da1",
    "re1e4_f270": "#577590",
    "re5e4_f180": "#43aa8b",
    "re1e5_f180": "#f9a620",
    "re1e6_f270_mature": "#d62828",
}


def _trapz(values: np.ndarray, coords: np.ndarray, axis: int = -1) -> np.ndarray:
    function = getattr(np, "trapezoid", np.trapz)
    return function(values, x=coords, axis=axis)


def _trapz2(values: np.ndarray, x: np.ndarray, y: np.ndarray) -> float:
    return float(_trapz(_trapz(values, y, axis=1), x, axis=0))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def raw_step(path: Path) -> int:
    match = re.fullmatch(r"lustre_(\d+)\.dat", path.name)
    if match is None:
        raise ValueError(f"not a numbered restart field: {path}")
    return int(match.group(1))


def infer_grid(path: Path) -> GridSpec:
    size = path.stat().st_size
    if size not in GRID_BY_BYTES:
        supported = ", ".join(f"{g.name}={g.field_bytes}" for g in GRIDS)
        raise RuntimeError(
            f"unsupported/truncated raw field {path}: {size} bytes; expected {supported}"
        )
    return GRID_BY_BYTES[size]


def _cell_centres(
    restart_dir: Path,
    axis: str,
    cells: int,
    fallback_bounds: tuple[float, float],
) -> np.ndarray:
    path = restart_dir / f"lustre_{axis}_cb.dat"
    if path.is_file():
        boundaries = np.fromfile(path, dtype=FLOAT_DTYPE)
        if boundaries.size != cells + 1:
            raise RuntimeError(
                f"wrong {axis} grid length in {path}: {boundaries.size}, expected {cells + 1}"
            )
        if not np.isfinite(boundaries).all() or not np.all(np.diff(boundaries) > 0.0):
            raise RuntimeError(f"invalid or non-monotone grid coordinates: {path}")
        return 0.5 * (boundaries[:-1] + boundaries[1:])
    lo, hi = fallback_bounds
    spacing = (hi - lo) / cells
    return lo + (np.arange(cells, dtype=float) + 0.5) * spacing


def _crop_slice(coords: np.ndarray, lo: float, hi: float, halo: int = 6) -> slice:
    indices = np.flatnonzero((coords >= lo) & (coords <= hi))
    if indices.size < 8:
        raise RuntimeError(f"control-volume crop [{lo}, {hi}] does not resolve on grid")
    return slice(
        max(0, int(indices[0]) - halo),
        min(len(coords), int(indices[-1]) + halo + 1),
    )


def load_raw_state(path: Path, reynolds: float) -> SimpleNamespace:
    """Read only the widest control-volume crop from one float64 restart."""

    grid = infer_grid(path)
    restart = path.parent
    x_all = _cell_centres(restart, "x", grid.nx, grid.x_bounds)
    y_all = _cell_centres(restart, "y", grid.ny, grid.y_bounds)
    widest = CV_BY_NAME["wide"]
    xs = _crop_slice(x_all, widest.xmin, widest.xmax)
    ys = _crop_slice(y_all, widest.ymin, widest.ymax)
    x = np.asarray(x_all[xs], dtype=float)
    y = np.asarray(y_all[ys], dtype=float)

    conservative = np.memmap(
        path,
        dtype=FLOAT_DTYPE,
        mode="r",
        shape=(NVAR, grid.ny, grid.nx),
        order="C",
    )
    rho = np.array(conservative[0, ys, xs].T, dtype=float, copy=True)
    mom_x = np.array(conservative[1, ys, xs].T, dtype=float, copy=True)
    mom_y = np.array(conservative[2, ys, xs].T, dtype=float, copy=True)
    energy = np.array(conservative[3, ys, xs].T, dtype=float, copy=True)
    del conservative

    if not all(np.isfinite(a).all() for a in (rho, mom_x, mom_y, energy)):
        raise RuntimeError(f"non-finite conservative field: {path}")
    xx, yy = x[:, None], y[None, :]
    clipped = np.clip(xx, 0.0, 1.0)
    half = AIRFOIL_HALF_HEIGHT * (1.0 - np.abs(2.0 * clipped - 1.0))
    fluid = ~((xx >= 0.0) & (xx <= 1.0) & (np.abs(yy) <= half))
    if np.any(rho[fluid] <= 0.0):
        raise RuntimeError(f"non-positive fluid density: {path}")
    denominator = np.where(rho > 0.0, rho, 1.0)
    u = mom_x / denominator
    v = mom_y / denominator
    pressure = (energy - 0.5 * (mom_x * u + mom_y * v)) / MFC_GAMMA_PARAMETER
    if not all(np.isfinite(a).all() for a in (u, v, pressure)):
        raise RuntimeError(f"non-finite primitive field: {path}")
    if np.any(pressure[fluid] <= 0.0):
        raise RuntimeError(f"non-positive fluid pressure: {path}")

    du_dx, du_dy = np.gradient(u, x, y, edge_order=2)
    dv_dx, dv_dy = np.gradient(v, x, y, edge_order=2)
    mu = RHO_INF * U_INF * CHORD / reynolds
    divergence = du_dx + dv_dy
    tau_xx = mu * (2.0 * du_dx - (2.0 / 3.0) * divergence)
    tau_yy = mu * (2.0 * dv_dy - (2.0 / 3.0) * divergence)
    tau_xy = mu * (du_dy + dv_dx)
    return SimpleNamespace(
        x=x,
        y=y,
        rho=rho,
        mom_x=mom_x,
        mom_y=mom_y,
        pressure=pressure,
        u=u,
        v=v,
        tau_xx=tau_xx,
        tau_xy=tau_xy,
        tau_yy=tau_yy,
        grid=grid.name,
        source=str(path.resolve()),
    )


def bilinear_points(
    x: np.ndarray,
    y: np.ndarray,
    field: np.ndarray,
    xp: np.ndarray | float,
    yp: np.ndarray | float,
) -> np.ndarray:
    xp_array, yp_array = np.broadcast_arrays(
        np.asarray(xp, dtype=float), np.asarray(yp, dtype=float)
    )
    if (
        np.any(xp_array < x[0])
        or np.any(xp_array > x[-1])
        or np.any(yp_array < y[0])
        or np.any(yp_array > y[-1])
    ):
        raise RuntimeError("interpolation point lies outside loaded raw-field crop")
    ix = np.clip(np.searchsorted(x, xp_array, side="right") - 1, 0, len(x) - 2)
    iy = np.clip(np.searchsorted(y, yp_array, side="right") - 1, 0, len(y) - 2)
    wx = (xp_array - x[ix]) / (x[ix + 1] - x[ix])
    wy = (yp_array - y[iy]) / (y[iy + 1] - y[iy])
    return (
        (1.0 - wx) * (1.0 - wy) * field[ix, iy]
        + wx * (1.0 - wy) * field[ix + 1, iy]
        + (1.0 - wx) * wy * field[ix, iy + 1]
        + wx * wy * field[ix + 1, iy + 1]
    )


def _integration_axis(coords: np.ndarray, lo: float, hi: float) -> np.ndarray:
    interior = coords[(coords > lo) & (coords < hi)]
    return np.concatenate(([lo], interior, [hi]))


def _sample_rectangle(
    x: np.ndarray,
    y: np.ndarray,
    field: np.ndarray,
    cv: ControlVolume,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xi = _integration_axis(x, cv.xmin, cv.xmax)
    yi = _integration_axis(y, cv.ymin, cv.ymax)
    xx, yy = np.meshgrid(xi, yi, indexing="ij")
    return xi, yi, bilinear_points(x, y, field, xx, yy)


def _boundary_flux(
    x: np.ndarray,
    y: np.ndarray,
    flux_x: np.ndarray,
    flux_y: np.ndarray,
    cv: ControlVolume,
) -> float:
    xi = _integration_axis(x, cv.xmin, cv.xmax)
    yi = _integration_axis(y, cv.ymin, cv.ymax)
    right = bilinear_points(x, y, flux_x, cv.xmax, yi)
    left = bilinear_points(x, y, flux_x, cv.xmin, yi)
    top = bilinear_points(x, y, flux_y, xi, cv.ymax)
    bottom = bilinear_points(x, y, flux_y, xi, cv.ymin)
    return float(_trapz(right - left, yi) + _trapz(top - bottom, xi))


def integrate_snapshot(state: SimpleNamespace, cv: ControlVolume) -> dict[str, float]:
    """Return stored momentum and outward momentum fluxes for one snapshot."""

    xi, yi, mx = _sample_rectangle(state.x, state.y, state.mom_x, cv)
    _, _, my = _sample_rectangle(state.x, state.y, state.mom_y, cv)
    storage_x = _trapz2(mx, xi, yi)
    storage_y = _trapz2(my, xi, yi)

    conv_x = _boundary_flux(
        state.x, state.y, state.mom_x * state.u, state.mom_x * state.v, cv
    )
    conv_y = _boundary_flux(
        state.x, state.y, state.mom_y * state.u, state.mom_y * state.v, cv
    )
    zeros = np.zeros_like(state.pressure)
    pressure_x = _boundary_flux(
        state.x, state.y, state.pressure, zeros, cv
    )
    pressure_y = _boundary_flux(
        state.x, state.y, zeros, state.pressure, cv
    )
    viscous_x = _boundary_flux(
        state.x, state.y, -state.tau_xx, -state.tau_xy, cv
    )
    viscous_y = _boundary_flux(
        state.x, state.y, -state.tau_xy, -state.tau_yy, cv
    )
    return {
        "storage_x": storage_x,
        "storage_y": storage_y,
        "convective_flux_x": conv_x,
        "convective_flux_y": conv_y,
        "pressure_flux_x": pressure_x,
        "pressure_flux_y": pressure_y,
        "viscous_flux_x": viscous_x,
        "viscous_flux_y": viscous_y,
    }


def rotate_force(force_x: float, force_y: float) -> tuple[float, float, float, float]:
    alpha = math.radians(ALPHA_DEG)
    drag = force_x * math.cos(alpha) + force_y * math.sin(alpha)
    lift = -force_x * math.sin(alpha) + force_y * math.cos(alpha)
    return drag, lift, drag / (Q_INF * CHORD), lift / (Q_INF * CHORD)


def _gradient(values: Sequence[float], times: Sequence[float]) -> np.ndarray:
    edge_order = 2 if len(values) >= 3 else 1
    return np.gradient(np.asarray(values, dtype=float), np.asarray(times, dtype=float), edge_order=edge_order)


def finish_history(
    base_rows: list[dict[str, Any]],
    spec: CaseSpec,
    cv: ControlVolume,
) -> list[dict[str, Any]]:
    times = [float(row["time"]) for row in base_rows]
    dpx = _gradient([row["storage_x"] for row in base_rows], times)
    dpy = _gradient([row["storage_y"] for row in base_rows], times)
    result: list[dict[str, Any]] = []
    for index, (row, storage_rate_x, storage_rate_y) in enumerate(zip(base_rows, dpx, dpy)):
        components = {
            "storage_force_x": -float(storage_rate_x),
            "storage_force_y": -float(storage_rate_y),
            "convective_force_x": -float(row["convective_flux_x"]),
            "convective_force_y": -float(row["convective_flux_y"]),
            "pressure_boundary_force_x": -float(row["pressure_flux_x"]),
            "pressure_boundary_force_y": -float(row["pressure_flux_y"]),
            "viscous_boundary_force_x": -float(row["viscous_flux_x"]),
            "viscous_boundary_force_y": -float(row["viscous_flux_y"]),
        }
        force_x = sum(value for key, value in components.items() if key.endswith("_x"))
        force_y = sum(value for key, value in components.items() if key.endswith("_y"))
        drag, lift, cd, cl = rotate_force(force_x, force_y)
        result.append(
            {
                "case": spec.case,
                "Re_c": spec.reynolds,
                "grid": spec.grid,
                "stage": spec.stage,
                "window": spec.window,
                "comparison_role": spec.comparison_role,
                "control_volume": cv.name,
                "cv_xmin": cv.xmin,
                "cv_xmax": cv.xmax,
                "cv_ymin": cv.ymin,
                "cv_ymax": cv.ymax,
                "step": int(row["step"]),
                "time": float(row["time"]),
                "source_file": row["source_file"],
                **components,
                "force_x": force_x,
                "force_y": force_y,
                "drag": drag,
                "lift": lift,
                "CD": cd,
                "CL": cl,
                "endpoint_derivative": index in (0, len(base_rows) - 1),
                "method": METHOD,
                "claim_level": CLAIM_LEVEL,
            }
        )
    return result


def inspect_native_record(path: Path) -> tuple[str, float | None, float | None]:
    if not path.is_file():
        return "MISSING", None, None
    if path.stat().st_size != IB_RECORD_BYTES:
        return f"WRONG_SIZE_{path.stat().st_size}", None, None
    values = struct.unpack(f"={IB_RECORD_WIDTH}d", path.read_bytes())
    fx, fy = float(values[1]), float(values[2])
    if not (math.isfinite(fx) and math.isfinite(fy)):
        return "NONFINITE_FX_FY", fx, fy
    return "FINITE_FX_FY", fx, fy


def case_specs(
    re1e4_root: Path,
    ladder_root: Path,
    re1e6_initial: Path,
    long_chain: Path,
) -> list[CaseSpec]:
    del re1e6_initial  # retained in provenance; mature t=26--31 is analyzed here.
    return [
        CaseSpec("re1e4_f180", 1.0e4, "f180", "t00_t06", re1e4_root / "f180", 1.0 / 3600.0, 10800, 21600, 180, "t=3--6", "same_grid_reynolds"),
        CaseSpec("re1e4_f270", 1.0e4, "f270", "t00_t06", re1e4_root / "f270", 1.0 / 5400.0, 16200, 32400, 270, "t=3--6", "grid_sensitivity"),
        CaseSpec("re5e4_f180", 5.0e4, "f180", "t00_t06", ladder_root / "re5e4", 1.0 / 3600.0, 10800, 21600, 360, "t=3--6", "same_grid_reynolds"),
        CaseSpec("re1e5_f180", 1.0e5, "f180", "t00_t06", ladder_root / "re1e5", 1.0 / 3600.0, 10800, 21600, 360, "t=3--6", "same_grid_reynolds"),
        CaseSpec("re1e6_f270_mature", 1.0e6, "f270", "t26_t31", long_chain / "t26_t31", 1.0 / 5400.0, 140400, 167400, 270, "t=26--31", "mature_context_mixed_grid_window"),
    ]


def scan_case(spec: CaseSpec) -> tuple[list[Path], dict[str, Any]]:
    restart = spec.case_dir / "restart_data"
    expected_steps = list(range(spec.start_step, spec.stop_step + 1, spec.stride))
    files: list[Path] = []
    missing: list[int] = []
    wrong_grid: list[str] = []
    native_counts: dict[str, int] = {}
    for step in expected_steps:
        field = restart / f"lustre_{step}.dat"
        if not field.is_file():
            missing.append(step)
            continue
        try:
            grid = infer_grid(field)
        except RuntimeError as exc:
            wrong_grid.append(str(exc))
            continue
        if grid.name != spec.grid:
            wrong_grid.append(f"{field}: inferred {grid.name}, expected {spec.grid}")
            continue
        files.append(field.resolve())
        native_status, _, _ = inspect_native_record(restart / f"ib_state_{step}.dat")
        native_counts[native_status] = native_counts.get(native_status, 0) + 1
    coverage = len(files) / len(expected_steps)
    usable = len(files) >= 5 and coverage >= 0.90 and not wrong_grid
    inventory = {
        "case": spec.case,
        "Re_c": spec.reynolds,
        "grid": spec.grid,
        "stage": spec.stage,
        "window": spec.window,
        "case_dir": str(spec.case_dir.resolve()),
        "expected_files": len(expected_steps),
        "usable_files": len(files),
        "coverage_fraction": coverage,
        "first_step": expected_steps[0],
        "last_step": expected_steps[-1],
        "missing_steps": " ".join(map(str, missing)),
        "wrong_grid_errors": " | ".join(wrong_grid),
        "native_force_status": NATIVE_FORCE_STATUS,
        "native_finite_fx_fy": native_counts.get("FINITE_FX_FY", 0),
        "native_nonfinite_fx_fy": native_counts.get("NONFINITE_FX_FY", 0),
        "native_missing": native_counts.get("MISSING", 0),
        "status": "PASS" if usable and coverage == 1.0 else ("QUALIFIED" if usable else "FAILED"),
    }
    if not usable:
        raise RuntimeError(
            f"raw-field window unusable for {spec.case}: {len(files)}/{len(expected_steps)}, "
            f"wrong_grid={len(wrong_grid)}"
        )
    return files, inventory


def process_case(spec: CaseSpec, files: list[Path], progress: list[int]) -> list[dict[str, Any]]:
    per_cv: dict[str, list[dict[str, Any]]] = {cv.name: [] for cv in CONTROL_VOLUMES}
    for path in files:
        step = raw_step(path)
        state = load_raw_state(path, spec.reynolds)
        if state.grid != spec.grid:
            raise RuntimeError(f"grid changed while reading {path}")
        for cv in CONTROL_VOLUMES:
            per_cv[cv.name].append(
                {
                    "step": step,
                    "time": step * spec.dt,
                    "source_file": str(path),
                    **integrate_snapshot(state, cv),
                }
            )
        progress[0] += 1
        print(
            f"CV_FORCE_FRAME {progress[0]}/{progress[1]} "
            f"case={spec.case} step={step} time={step * spec.dt:.6f}",
            flush=True,
        )
    rows: list[dict[str, Any]] = []
    for cv in CONTROL_VOLUMES:
        rows.extend(finish_history(per_cv[cv.name], spec, cv))
    return rows


def _stats(values: Sequence[float]) -> tuple[float, float, float]:
    array = np.asarray(values, dtype=float)
    return float(np.mean(array)), float(np.std(array, ddof=1)), float(np.ptp(array))


def summarize(history: list[dict[str, Any]], inventories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inventory_by_case = {row["case"]: row for row in inventories}
    rows: list[dict[str, Any]] = []
    for case in CASE_ORDER:
        case_rows = [row for row in history if row["case"] == case]
        if not case_rows:
            continue
        for cv in CONTROL_VOLUMES:
            selected = [row for row in case_rows if row["control_volume"] == cv.name]
            # One-sided endpoint derivatives are retained in history but excluded
            # from window statistics to reduce differentiation bias.
            interior = [row for row in selected if not row["endpoint_derivative"]]
            if len(interior) < 3:
                interior = selected
            cd_mean, cd_std, cd_ptp = _stats([row["CD"] for row in interior])
            cl_mean, cl_std, cl_ptp = _stats([row["CL"] for row in interior])
            drag_mean = float(np.mean([row["drag"] for row in interior]))
            lift_mean = float(np.mean([row["lift"] for row in interior]))
            rows.append(
                {
                    "case": case,
                    "Re_c": selected[0]["Re_c"],
                    "grid": selected[0]["grid"],
                    "window": selected[0]["window"],
                    "comparison_role": selected[0]["comparison_role"],
                    "control_volume": cv.name,
                    "samples_total": len(selected),
                    "samples_statistics": len(interior),
                    "coverage_fraction": inventory_by_case[case]["coverage_fraction"],
                    "CL_mean": cl_mean,
                    "CL_temporal_std": cl_std,
                    "CL_peak_to_peak": cl_ptp,
                    "CD_mean": cd_mean,
                    "CD_temporal_std": cd_std,
                    "CD_peak_to_peak": cd_ptp,
                    "lift_mean": lift_mean,
                    "drag_mean": drag_mean,
                    "L_over_D_from_means": lift_mean / drag_mean if abs(drag_mean) > 1.0e-12 else math.nan,
                    "method": METHOD,
                    "claim_level": CLAIM_LEVEL,
                }
            )

    by_case_cv = {(row["case"], row["control_volume"]): row for row in rows}
    for case in CASE_ORDER:
        if (case, NOMINAL_CV) not in by_case_cv:
            continue
        nominal = by_case_cv[(case, NOMINAL_CV)]
        cl_values = [by_case_cv[(case, cv.name)]["CL_mean"] for cv in CONTROL_VOLUMES]
        cd_values = [by_case_cv[(case, cv.name)]["CD_mean"] for cv in CONTROL_VOLUMES]
        cl_spread = float(np.ptp(cl_values))
        cd_spread = float(np.ptp(cd_values))
        for cv in CONTROL_VOLUMES:
            row = by_case_cv[(case, cv.name)]
            row["CL_cv_spread"] = cl_spread
            row["CD_cv_spread"] = cd_spread
            row["CL_cv_spread_percent_of_nominal"] = 100.0 * cl_spread / max(abs(nominal["CL_mean"]), 1.0e-12)
            row["CD_cv_spread_percent_of_nominal"] = 100.0 * cd_spread / max(abs(nominal["CD_mean"]), 1.0e-12)
    return rows


def comparisons(summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nominal = {
        row["case"]: row
        for row in summary
        if row["control_volume"] == NOMINAL_CV
    }
    pairs = (
        ("re1e4_f180", "re5e4_f180", "same_grid_reynolds"),
        ("re5e4_f180", "re1e5_f180", "same_grid_reynolds"),
        ("re1e4_f180", "re1e5_f180", "same_grid_reynolds"),
        ("re1e4_f180", "re1e4_f270", "grid_sensitivity"),
        ("re1e5_f180", "re1e6_f270_mature", "mixed_grid_and_time_context_only"),
    )
    result: list[dict[str, Any]] = []
    for baseline, target, kind in pairs:
        if baseline not in nominal or target not in nominal:
            continue
        left, right = nominal[baseline], nominal[target]
        result.append(
            {
                "comparison": f"{target}_minus_{baseline}",
                "kind": kind,
                "baseline": baseline,
                "target": target,
                "delta_CL": right["CL_mean"] - left["CL_mean"],
                "delta_CD": right["CD_mean"] - left["CD_mean"],
                "relative_CL_percent": 100.0 * (right["CL_mean"] - left["CL_mean"]) / max(abs(left["CL_mean"]), 1.0e-12),
                "relative_CD_percent": 100.0 * (right["CD_mean"] - left["CD_mean"]) / max(abs(left["CD_mean"]), 1.0e-12),
                "interpretation_limit": (
                    "direct Reynolds trend at common f180 grid and t=3--6"
                    if kind == "same_grid_reynolds"
                    else "not an isolated Reynolds effect"
                ),
            }
        )
    return result


def write_csv(path: Path, rows: list[dict[str, Any]], fields: Sequence[str] | None = None) -> None:
    if not rows:
        raise RuntimeError(f"cannot write empty CSV: {path}")
    columns = list(fields) if fields is not None else list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def json_safe(value: Any) -> Any:
    """Replace non-finite scalar floats with JSON null without hiding CSV data."""

    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (float, np.floating)) and not math.isfinite(float(value)):
        return None
    if isinstance(value, np.integer):
        return int(value)
    return value


def _nominal(summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_case = {
        row["case"]: row for row in summary if row["control_volume"] == NOMINAL_CV
    }
    return [by_case[case] for case in CASE_ORDER if case in by_case]


def _airfoil_patch(ax: plt.Axes) -> None:
    points = np.array(
        [[0.0, 0.0], [0.5, AIRFOIL_HALF_HEIGHT], [1.0, 0.0], [0.5, -AIRFOIL_HALF_HEIGHT]]
    )
    ax.add_patch(Polygon(points, closed=True, facecolor="0.2", edgecolor="white", lw=0.7))


def make_email_figure(summary: list[dict[str, Any]], path: Path) -> None:
    nominal = _nominal(summary)
    f180 = [row for row in nominal if row["comparison_role"] == "same_grid_reynolds"]
    mature = [row for row in nominal if row["case"] == "re1e6_f270_mature"]
    fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.8))
    for ax, mean_key, std_key, label in (
        (axes[0], "CL_mean", "CL_temporal_std", r"$C_L$"),
        (axes[1], "CD_mean", "CD_temporal_std", r"$C_D$"),
    ):
        ax.errorbar(
            [row["Re_c"] for row in f180],
            [row[mean_key] for row in f180],
            yerr=[row[std_key] for row in f180],
            color="#1f5f8b",
            marker="o",
            lw=2.0,
            capsize=4,
            label="f180, t=3--6 (direct trend)",
        )
        if mature:
            row = mature[0]
            ax.errorbar(
                [row["Re_c"]], [row[mean_key]], yerr=[row[std_key]],
                color=COLORS[row["case"]], marker="s", mfc="white", ms=7,
                capsize=4, linestyle="none", label="f270, t=26--31 (context)",
            )
        ax.set_xscale("log")
        ax.set_xlabel(r"Chord Reynolds number, $Re_c$")
        ax.set_ylabel(label)
        ax.grid(True, which="both", alpha=0.25)
        ax.axhline(0.0, color="0.4", lw=0.7)
    axes[0].legend(frameon=False, fontsize=8, loc="best")
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.20, top=0.84, wspace=0.16)
    fig.suptitle(
        r"Mach 3 diamond airfoil, $\alpha=40^\circ$: reconstructed total loads",
        y=0.965,
        fontsize=13,
        fontweight="bold",
    )
    fig.text(
        0.5, 0.035,
        "Symbols: window mean; bars: temporal standard deviation. "
        "Control-volume momentum balance; native MFC IB loads are NaN.",
        ha="center", va="bottom", fontsize=8, color="0.25",
    )
    fig.savefig(path, dpi=240, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _summary_table(ax: plt.Axes, summary: list[dict[str, Any]]) -> None:
    nominal = _nominal(summary)
    ax.axis("off")
    columns = ["Case", "Window", "CL mean ± σt", "CD mean ± σt", "L/D", "CV spread CL/CD"]
    cells = []
    for row in nominal:
        cells.append(
            [
                row["case"],
                row["window"],
                f"{row['CL_mean']:.4g} ± {row['CL_temporal_std']:.2g}",
                f"{row['CD_mean']:.4g} ± {row['CD_temporal_std']:.2g}",
                f"{row['L_over_D_from_means']:.3g}",
                f"{row['CL_cv_spread_percent_of_nominal']:.1f}% / {row['CD_cv_spread_percent_of_nominal']:.1f}%",
            ]
        )
    table = ax.table(cellText=cells, colLabels=columns, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(7.5)
    table.scale(1.0, 1.65)
    for (row, _), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#dceaf4")
            cell.set_text_props(weight="bold")


def make_pdf(
    history: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    inventories: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
    email_figure: Path,
    path: Path,
) -> None:
    nominal = _nominal(summary)
    with PdfPages(path) as pdf:
        fig = plt.figure(figsize=(11.0, 8.5), constrained_layout=True)
        grid = fig.add_gridspec(3, 1, height_ratios=(0.7, 1.65, 0.65))
        ax_title = fig.add_subplot(grid[0]); ax_title.axis("off")
        ax_title.text(0.0, 0.86, "Mach 3 diamond airfoil: Reynolds-number force evidence", fontsize=18, weight="bold")
        ax_title.text(0.0, 0.55, "Total lift and drag reconstructed from raw float64 MFC fields", fontsize=12)
        ax_title.text(0.0, 0.19, f"Method: {METHOD}\nClaim level: {CLAIM_LEVEL}\nNative IB load status: {NATIVE_FORCE_STATUS}", fontsize=9, family="monospace")
        _summary_table(fig.add_subplot(grid[1]), summary)
        ax_note = fig.add_subplot(grid[2]); ax_note.axis("off")
        ax_note.text(
            0.0, 0.96,
            "Interpretation. The f180 points at Re=10^4, 5×10^4, and 10^5 use the same grid and t=3--6 window. "
            "The Re=10^6 point uses f270 and the mature t=26--31 window, so it is contextual rather than an isolated Reynolds comparison.\n\n"
            "Uncertainty labels. Temporal standard deviation measures flow variability, not uncertainty. "
            "Spread across compact/nominal/wide control volumes measures method sensitivity. "
            "Boundary pressure and viscous flux terms are not a wall-force decomposition.",
            va="top", fontsize=9, wrap=True,
        )
        pdf.savefig(fig); plt.close(fig)

        image = plt.imread(email_figure)
        fig, ax = plt.subplots(figsize=(11.0, 6.5), constrained_layout=True)
        ax.imshow(image); ax.axis("off")
        pdf.savefig(fig); plt.close(fig)

        fig, axes = plt.subplots(2, 1, figsize=(11.0, 8.5), sharex=False, constrained_layout=True)
        for case in CASE_ORDER:
            rows = [r for r in history if r["case"] == case and r["control_volume"] == NOMINAL_CV]
            if not rows:
                continue
            axes[0].plot([r["time"] for r in rows], [r["CL"] for r in rows], color=COLORS[case], lw=1.2, label=CASE_LABELS[case])
            axes[1].plot([r["time"] for r in rows], [r["CD"] for r in rows], color=COLORS[case], lw=1.2, label=CASE_LABELS[case])
        axes[0].set_ylabel(r"$C_L$"); axes[1].set_ylabel(r"$C_D$"); axes[1].set_xlabel("Nondimensional time")
        axes[0].set_title("Nominal-control-volume histories")
        for ax in axes: ax.grid(True, alpha=0.25)
        axes[0].legend(frameon=False, fontsize=8, ncol=2)
        pdf.savefig(fig); plt.close(fig)

        fig, axes = plt.subplots(1, 2, figsize=(11.0, 7.0), constrained_layout=True)
        xloc = np.arange(len(CASE_ORDER))
        width = 0.24
        for offset, cv in zip((-width, 0.0, width), CONTROL_VOLUMES):
            rows = {r["case"]: r for r in summary if r["control_volume"] == cv.name}
            axes[0].bar(xloc + offset, [rows[c]["CL_mean"] for c in CASE_ORDER], width, label=cv.name)
            axes[1].bar(xloc + offset, [rows[c]["CD_mean"] for c in CASE_ORDER], width, label=cv.name)
        for ax, label in zip(axes, (r"mean $C_L$", r"mean $C_D$")):
            ax.set_xticks(xloc, CASE_ORDER, rotation=28, ha="right", fontsize=8)
            ax.set_ylabel(label); ax.grid(True, axis="y", alpha=0.25)
        axes[0].set_title("Control-volume sensitivity")
        axes[0].legend(frameon=False)
        pdf.savefig(fig); plt.close(fig)

        fig = plt.figure(figsize=(11.0, 8.5), constrained_layout=True)
        ax = fig.add_subplot(111); ax.axis("off")
        lines = [
            "DATA PROVENANCE AND QUALITY LIMITS", "",
            "Each load history comes from the float64 conservative restart fields. No CFD was rerun.",
            "The stored native ib_state Fx/Fy slots were audited and are non-finite for the requested windows.", "",
            "SOURCE INVENTORY",
        ]
        for row in inventories:
            lines.append(
                f"  {row['case']}: raw={row['usable_files']}/{row['expected_files']} "
                f"({100*row['coverage_fraction']:.1f}%), native NaN={row['native_nonfinite_fx_fy']}, "
                f"native finite={row['native_finite_fx_fy']}, status={row['status']}"
            )
        lines.extend(["", "COMPARISONS"])
        for row in comparison_rows:
            lines.append(
                f"  {row['comparison']}: ΔCL={row['delta_CL']:.4g}, ΔCD={row['delta_CD']:.4g}; "
                f"{row['kind']}"
            )
        lines.extend([
            "", "EQUATION",
            "  F_body = -d/dt integral_CV(rho*u)dA - boundary_integral_CV((rho*u*u+p*I-tau).n)ds",
            f"  alpha={ALPHA_DEG:g} deg, rho_inf={RHO_INF:g}, U_inf={U_INF:g}, chord={CHORD:g}, q_inf={Q_INF:g}",
            "  mu = rho_inf*U_inf*chord/Re_c; compressible Newtonian Stokes stress.",
            "", "RECOMMENDED USE",
            "  Email Tim the compact PNG and, if useful, attach this PDF. Treat the CSV files as audit support.",
            "  Do not label the reconstructed loads as native MFC IB forces or as a converged force prediction.",
        ])
        ax.text(0.02, 0.98, "\n".join(lines), va="top", family="monospace", fontsize=8.5)
        pdf.savefig(fig); plt.close(fig)


def make_readme(status: str, output: Path) -> None:
    text = f"""# Read me first: reconstructed MFC forces

Status: **{status}**

The native MFC `ib_state` files store non-finite `Fx/Fy` for essentially all
requested snapshots. This package therefore reports **reconstructed total
loads** from the conservative momentum balance on fixed control volumes using
the original float64 restart fields. No CFD calculation was rerun.

## What to send Tim Colonius

1. `TIM_COLONIUS_REYNOLDS_FORCE_TRENDS.png` — minimal email figure.
2. `TIM_COLONIUS_CONTROL_VOLUME_FORCES.pdf` — method, histories, sensitivity,
   and provenance if he wants supporting detail.

## Scientific limits

- Direct Reynolds comparison: f180, `Re_c=1e4, 5e4, 1e5`, common `t=3--6`.
- `Re_c=1e6` is mature `t=26--31` on f270 and is context only.
- Error bars in the figure are temporal standard deviations (flow variability).
- Compact/nominal/wide spread is method sensitivity.
- Boundary pressure/viscous flux contributions are **not** a surface
  pressure/skin-friction decomposition.
- Claim level: `{CLAIM_LEVEL}`.

## Machine-readable files

- `control_volume_force_history.csv`: nominal-CV time series.
- `control_volume_force_history_all.csv`: all three CV time series and terms.
- `control_volume_force_summary.csv`: mean, temporal variability, and CV spread.
- `control_volume_force_comparisons.csv`: explicit grid/Reynolds comparisons.
- `control_volume_force_inventory.csv`: source coverage and native-NaN audit.
- `control_volume_force_report.json`: complete metadata.

Output directory: `{output}`
"""
    (output / "READ_ME_FIRST.md").write_text(text, encoding="utf-8")


def self_test() -> None:
    x = np.linspace(-1.2, 2.2, 171)
    y = np.linspace(-1.1, 1.1, 133)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    zeros = np.zeros_like(xx)
    rho = np.ones_like(xx)
    u = np.full_like(xx, 2.0)
    v = np.full_like(xx, -0.5)
    uniform = SimpleNamespace(
        x=x, y=y, rho=rho, mom_x=rho*u, mom_y=rho*v, pressure=np.full_like(xx, 0.8),
        u=u, v=v, tau_xx=zeros, tau_xy=zeros, tau_yy=zeros,
    )
    for cv in CONTROL_VOLUMES:
        terms = integrate_snapshot(uniform, cv)
        for key in (
            "convective_flux_x", "convective_flux_y", "pressure_flux_x",
            "pressure_flux_y", "viscous_flux_x", "viscous_flux_y",
        ):
            if not math.isclose(terms[key], 0.0, abs_tol=2.0e-11):
                raise AssertionError(f"uniform-field test failed for {cv.name} {key}: {terms[key]}")

    a, b, p0 = 0.17, -0.11, 2.0
    linear = SimpleNamespace(**vars(uniform))
    linear.pressure = p0 + a * xx + b * yy
    normalized: list[tuple[float, float]] = []
    for cv in CONTROL_VOLUMES:
        terms = integrate_snapshot(linear, cv)
        fx = -terms["pressure_flux_x"]
        fy = -terms["pressure_flux_y"]
        expected = (-a * cv.area, -b * cv.area)
        if not (math.isclose(fx, expected[0], rel_tol=1.0e-10, abs_tol=1.0e-10) and math.isclose(fy, expected[1], rel_tol=1.0e-10, abs_tol=1.0e-10)):
            raise AssertionError(f"linear-pressure test failed for {cv.name}: {(fx, fy)} != {expected}")
        normalized.append((fx / cv.area, fy / cv.area))
    if max(abs(item[0] + a) + abs(item[1] + b) for item in normalized) > 1.0e-10:
        raise AssertionError("analytic CV-size normalization test failed")

    drag, lift, cd, cl = rotate_force(Q_INF, 0.0)
    alpha = math.radians(ALPHA_DEG)
    if not (
        math.isclose(drag, Q_INF * math.cos(alpha), rel_tol=1.0e-12)
        and math.isclose(lift, -Q_INF * math.sin(alpha), rel_tol=1.0e-12)
        and math.isclose(cd, math.cos(alpha), rel_tol=1.0e-12)
        and math.isclose(cl, -math.sin(alpha), rel_tol=1.0e-12)
    ):
        raise AssertionError("force rotation/normalization test failed")

    with tempfile.TemporaryDirectory(prefix="mfc-cv-force-self-test-") as directory:
        root = Path(directory)
        csv_path = root / "synthetic.csv"
        write_csv(csv_path, [{"test": "linear_pressure", "Fx": -a, "Fy": -b}])
        png_path = root / "synthetic.png"
        fig, ax = plt.subplots(figsize=(4, 2.5)); ax.plot([0, 1], [0, 1]); ax.set_title("Synthetic CV test")
        fig.savefig(png_path, dpi=120); plt.close(fig)
        pdf_path = root / "synthetic.pdf"
        with PdfPages(pdf_path) as pdf:
            fig, ax = plt.subplots(figsize=(5, 3)); ax.plot([0, 1], [1, 0]); ax.set_title("Synthetic control-volume test")
            pdf.savefig(fig); plt.close(fig)
        archive = root / "synthetic.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for item in (csv_path, png_path, pdf_path):
                bundle.write(item, item.name)
        for item in (csv_path, png_path, pdf_path, archive):
            if not item.is_file() or item.stat().st_size == 0:
                raise AssertionError(f"self-test artifact missing: {item}")
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip() is not None:
                raise AssertionError("self-test ZIP failed CRC validation")
    print("MFC_CONTROL_VOLUME_FORCE_SELF_TEST=PASS")


def run(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.expanduser().resolve()
    # The submitter creates the directory first to place provenance and Slurm
    # files in it. Refuse only a previously completed analysis, not that shell.
    output.mkdir(parents=True, exist_ok=True)
    if (output / "ANALYSIS_COMPLETE.txt").exists():
        raise RuntimeError(f"analysis output is already complete: {output}")
    specs = case_specs(args.re1e4_root, args.ladder_root, args.re1e6_initial, args.long_chain)
    inventories: list[dict[str, Any]] = []
    files_by_case: dict[str, list[Path]] = {}
    for spec in specs:
        files, inventory = scan_case(spec)
        files_by_case[spec.case] = files
        inventories.append(inventory)

    total = sum(len(files) for files in files_by_case.values())
    progress = [0, total]
    history: list[dict[str, Any]] = []
    for spec in specs:
        history.extend(process_case(spec, files_by_case[spec.case], progress))
    if progress[0] != total:
        raise RuntimeError(f"internal progress mismatch: {progress[0]} != {total}")

    summary = summarize(history, inventories)
    comparison_rows = comparisons(summary)
    nominal_history = [row for row in history if row["control_volume"] == NOMINAL_CV]
    write_csv(output / "control_volume_force_history_all.csv", history)
    write_csv(output / "control_volume_force_history.csv", nominal_history)
    write_csv(output / "control_volume_force_summary.csv", summary)
    write_csv(output / "control_volume_force_comparisons.csv", comparison_rows)
    write_csv(output / "control_volume_force_inventory.csv", inventories)

    maximum_sensitivity = max(
        max(row["CL_cv_spread_percent_of_nominal"], row["CD_cv_spread_percent_of_nominal"])
        for row in summary if row["control_volume"] == NOMINAL_CV
    )
    complete_sources = all(row["status"] == "PASS" for row in inventories)
    status = "PASS" if complete_sources and maximum_sensitivity <= args.qualified_sensitivity_percent else "QUALIFIED"
    email_figure = output / "TIM_COLONIUS_REYNOLDS_FORCE_TRENDS.png"
    pdf = output / "TIM_COLONIUS_CONTROL_VOLUME_FORCES.pdf"
    make_email_figure(summary, email_figure)
    make_pdf(history, summary, inventories, comparison_rows, email_figure, pdf)
    make_readme(status, output)

    report = {
        "status": status,
        "method": METHOD,
        "claim_level": CLAIM_LEVEL,
        "native_force_status": NATIVE_FORCE_STATUS,
        "equation": "F_body=-d/dt integral_CV(rho*u)dA-integral_boundary_CV(rho*u*u+pI-tau).n ds",
        "normalization": {"alpha_deg": ALPHA_DEG, "rho_inf": RHO_INF, "U_inf": U_INF, "chord": CHORD, "q_inf": Q_INF},
        "control_volumes": [asdict(cv) for cv in CONTROL_VOLUMES],
        "nominal_control_volume": NOMINAL_CV,
        "temporal_std_definition": "flow variability, not uncertainty",
        "cv_spread_definition": "method sensitivity, not statistical uncertainty",
        "boundary_term_warning": "not a wall pressure/skin-friction decomposition",
        "maximum_cv_sensitivity_percent": maximum_sensitivity,
        "qualified_threshold_percent": args.qualified_sensitivity_percent,
        "frames_processed": total,
        "inventories": inventories,
        "summary": summary,
        "comparisons": comparison_rows,
    }
    (output / "control_volume_force_report.json").write_text(
        json.dumps(json_safe(report), indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    marker = (
        f"status={status}\nmethod={METHOD}\nclaim_level={CLAIM_LEVEL}\n"
        f"native_force_status={NATIVE_FORCE_STATUS}\nframes_processed={total}\n"
        f"maximum_cv_sensitivity_percent={maximum_sensitivity:.8g}\n"
        "email_figure=TIM_COLONIUS_REYNOLDS_FORCE_TRENDS.png\n"
        "pdf=TIM_COLONIUS_CONTROL_VOLUME_FORCES.pdf\n"
        "summary=control_volume_force_summary.csv\n"
    )
    (output / "ANALYSIS_COMPLETE.txt").write_text(marker, encoding="utf-8")

    # Logs are still being appended by tee/Slurm while this process exits, so
    # checksum only immutable deliverables. The archive may contain the logs as
    # provenance, but it never promises hashes for volatile files.
    immutable_names = {
        "ANALYSIS_COMPLETE.txt",
        "READ_ME_FIRST.md",
        "TIM_COLONIUS_REYNOLDS_FORCE_TRENDS.png",
        "TIM_COLONIUS_CONTROL_VOLUME_FORCES.pdf",
        "control_volume_force_history.csv",
        "control_volume_force_history_all.csv",
        "control_volume_force_summary.csv",
        "control_volume_force_comparisons.csv",
        "control_volume_force_inventory.csv",
        "control_volume_force_report.json",
    }
    checksummed = sorted(output / name for name in immutable_names)
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in checksummed),
        encoding="utf-8",
    )
    if args.archive is not None:
        archive = args.archive.expanduser().resolve()
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(output.iterdir()):
                if path.is_file():
                    bundle.write(path, f"{output.name}/{path.name}")
        with zipfile.ZipFile(archive) as bundle:
            failed = bundle.testzip()
            if failed is not None:
                raise RuntimeError(f"archive CRC failure: {failed}")
        report["archive"] = str(archive)
    print(json.dumps({"status": status, "frames": total, "maximum_cv_sensitivity_percent": maximum_sensitivity}))
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--re1e4-root", type=Path)
    parser.add_argument("--ladder-root", type=Path)
    parser.add_argument("--re1e6-initial", type=Path)
    parser.add_argument("--long-chain", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--qualified-sensitivity-percent", type=float, default=15.0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return args
    required = ("re1e4_root", "ladder_root", "re1e6_initial", "long_chain", "output")
    missing = [name for name in required if getattr(args, name) is None]
    if missing:
        parser.error("missing required arguments: " + ", ".join("--" + name.replace("_", "-") for name in missing))
    if args.qualified_sensitivity_percent <= 0.0:
        parser.error("--qualified-sensitivity-percent must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
