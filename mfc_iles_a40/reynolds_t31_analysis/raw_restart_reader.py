#!/usr/bin/env python3
"""Read the serial MPI-IO restart fields written by the MFC A40 runs.

The production launchers in this repository run ``pre_process`` and
``simulation`` only.  Their time-resolved solutions therefore live in
``restart_data/lustre_<step>.dat``; ``binary/p*/<step>.dat`` exists only
after a separate MFC ``post_process`` run.  This module exposes the primitive
fields needed by the analysis without creating a second, very large copy of
the data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import numpy as np


NVAR = 5
FLOAT_DTYPE = np.dtype("<f8")
MFC_GAMMA_PARAMETER = 1.0 / (1.4 - 1.0)
AIRFOIL_HALF_HEIGHT = 0.0702704174


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


GRID_SPECS = (
    GridSpec("f180", 1980, 1800),
    GridSpec("f270", 2970, 2700),
)
GRID_BY_BYTES = {grid.field_bytes: grid for grid in GRID_SPECS}
STEP_PATTERN = re.compile(r"lustre_(\d+)\.dat$")


def raw_step(path: Path) -> int:
    match = STEP_PATTERN.fullmatch(path.name)
    if match is None:
        raise ValueError(f"not a numbered MFC restart field: {path}")
    return int(match.group(1))


def infer_grid(path: Path) -> GridSpec:
    try:
        return GRID_BY_BYTES[path.stat().st_size]
    except KeyError as exc:
        supported = ", ".join(
            f"{grid.name}={grid.field_bytes}" for grid in GRID_SPECS
        )
        raise RuntimeError(
            f"unsupported or truncated MFC restart field {path}: "
            f"{path.stat().st_size} bytes; expected one of {supported}"
        ) from exc


def discover_raw_fields(case_dir: Path) -> dict[int, Path]:
    """Return valid numbered restart fields, rejecting duplicate steps."""

    restart = Path(case_dir) / "restart_data"
    result: dict[int, Path] = {}
    for path in restart.glob("lustre_[0-9]*.dat"):
        if not path.is_file():
            continue
        step = raw_step(path)
        infer_grid(path)
        if step in result and result[step].resolve() != path.resolve():
            raise RuntimeError(f"duplicate raw field at step {step} under {case_dir}")
        result[step] = path.resolve()
    return dict(sorted(result.items()))


def discover_raw_steps(case_dir: Path) -> list[int]:
    return list(discover_raw_fields(case_dir))


def _cell_centres(
    restart: Path,
    axis: str,
    cells: int,
    fallback_bounds: tuple[float, float],
) -> np.ndarray:
    path = restart / f"lustre_{axis}_cb.dat"
    if path.is_file():
        boundaries = np.fromfile(path, dtype=FLOAT_DTYPE)
        if boundaries.size != cells + 1:
            raise RuntimeError(
                f"wrong {axis}-grid length in {path}: {boundaries.size}; "
                f"expected {cells + 1}"
            )
        if not np.isfinite(boundaries).all() or not np.all(np.diff(boundaries) > 0):
            raise RuntimeError(f"invalid or non-monotone grid coordinates in {path}")
        return 0.5 * (boundaries[:-1] + boundaries[1:])

    beginning, end = fallback_bounds
    spacing = (end - beginning) / cells
    return beginning + (np.arange(cells, dtype=float) + 0.5) * spacing


def _selection(coords: np.ndarray, bounds: tuple[float, float] | None, halo: int) -> slice:
    if bounds is None:
        return slice(None)
    indices = np.flatnonzero((coords >= bounds[0]) & (coords <= bounds[1]))
    if indices.size < 8:
        raise RuntimeError(f"requested bounds {bounds} do not resolve on the MFC grid")
    return slice(
        max(int(indices[0]) - halo, 0),
        min(int(indices[-1]) + halo + 1, len(coords)),
    )


def _fluid_mask(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    xx, yy = x[:, None], y[None, :]
    clipped = np.clip(xx, 0.0, 1.0)
    half = AIRFOIL_HALF_HEIGHT * (1.0 - np.abs(2.0 * clipped - 1.0))
    solid = (xx >= 0.0) & (xx <= 1.0) & (np.abs(yy) <= half)
    return ~solid


def assemble_raw(
    case_dir: Path | str,
    step: int,
    *,
    crop: tuple[float, float, float, float] | None = None,
    halo: int = 4,
) -> SimpleNamespace:
    """Return an object compatible with ``mfc.viz.reader.AssembledData``.

    MFC writes five conservative variables for this single-fluid 2-D case in
    variable-major order: density, x/y momentum, total energy, and volume
    fraction.  ``fluid_pp(1)%gamma`` is MFC's :math:`1/(gamma-1)` parameter,
    so pressure is ``(E - rho*u^2/2) / 2.5``.
    """

    case = Path(case_dir)
    source = case / "restart_data" / f"lustre_{int(step)}.dat"
    if not source.is_file():
        raise FileNotFoundError(f"raw MFC field not found: {source}")
    grid = infer_grid(source)
    restart = case / "restart_data"
    x_all = _cell_centres(restart, "x", grid.nx, grid.x_bounds)
    y_all = _cell_centres(restart, "y", grid.ny, grid.y_bounds)
    x_bounds = None if crop is None else (crop[0], crop[1])
    y_bounds = None if crop is None else (crop[2], crop[3])
    xs = _selection(x_all, x_bounds, halo)
    ys = _selection(y_all, y_bounds, halo)

    conservative = np.memmap(
        source,
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

    if not all(np.isfinite(value).all() for value in (rho, mom_x, mom_y, energy)):
        raise RuntimeError(f"non-finite conservative value in raw MFC field {source}")
    fluid = _fluid_mask(x_all[xs], y_all[ys])
    if np.any(rho[fluid] <= 0.0):
        raise RuntimeError(f"invalid density in raw MFC field {source}")
    # Some IB implementations leave zero density only inside the solid. Keep
    # those excluded cells finite without weakening any fluid-cell check.
    rho_denominator = np.where(rho > 0.0, rho, 1.0)
    vel_x = mom_x / rho_denominator
    vel_y = mom_y / rho_denominator
    dynamic_pressure = 0.5 * (mom_x * vel_x + mom_y * vel_y)
    pressure = (energy - dynamic_pressure) / MFC_GAMMA_PARAMETER
    if not all(np.isfinite(value).all() for value in (vel_x, vel_y, pressure)):
        raise RuntimeError(f"non-finite primitive values in raw MFC field {source}")
    if np.any(pressure[fluid] <= 0.0):
        raise RuntimeError(f"non-positive pressure in raw MFC field {source}")

    return SimpleNamespace(
        ndim=2,
        x_cc=np.asarray(x_all[xs], dtype=float),
        y_cc=np.asarray(y_all[ys], dtype=float),
        z_cc=np.asarray([0.0]),
        variables={
            "rho": rho,
            "pres": pressure,
            "vel1": vel_x,
            "vel2": vel_y,
        },
        source_path=str(source.resolve()),
        source_format="raw_restart_mpiio",
        grid=grid.name,
    )


def regular_coverage(
    steps: Iterable[int], start: int, stop: int, stride: int
) -> tuple[bool, list[int]]:
    available = set(int(step) for step in steps)
    missing = [step for step in range(start, stop + 1, stride) if step not in available]
    return not missing, missing
