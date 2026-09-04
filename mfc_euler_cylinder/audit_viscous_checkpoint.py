#!/usr/bin/env python3
"""Audit raw MFC cylinder checkpoints without assigning physical labels.

The tool reads the five conservative variables written by the pinned MFC
case, locates low-density cells relative to the immersed cylinder, reconstructs
pressure, and compares the density-implied viscous CFL with ``run_time.inf``.
It is a numerical-stability diagnostic, not evidence of a physical vortex or
of a validated Navier--Stokes solution.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path

import numpy as np


GAMMA = 1.4
RADIUS = 0.5
GRIDS = {
    "smoke": (120, 100, -2.0, 4.0, -2.5, 2.5),
    "f90": (990, 900, -5.0, 6.0, -5.0, 5.0),
    "f180": (1980, 1800, -5.0, 6.0, -5.0, 5.0),
    "f270": (2970, 2700, -5.0, 6.0, -5.0, 5.0),
}
STEP_PATTERN = re.compile(r"lustre_(\d+)\.dat$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart-dir", type=Path, required=True)
    parser.add_argument("--grid", choices=tuple(GRIDS), default="f180")
    parser.add_argument("--dt", type=float, required=True)
    parser.add_argument("--reynolds", type=float, required=True)
    parser.add_argument("--mach", type=float, required=True)
    parser.add_argument("--expected-final-step", type=int)
    parser.add_argument("--run-time-info", type=Path)
    parser.add_argument("--output-tsv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not args.restart_dir.is_dir():
        raise SystemExit(f"restart directory does not exist: {args.restart_dir}")
    for name in ("dt", "reynolds", "mach"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0.0:
            raise SystemExit(f"--{name} must be finite and positive")
    if args.mach <= 1.0:
        raise SystemExit("--mach must be supersonic")


def load_axis(path: Path, expected: int, fallback: np.ndarray) -> tuple[np.ndarray, str]:
    if not path.is_file():
        return fallback, "uniform_case_definition"
    values = np.fromfile(path, dtype="<f8")
    if values.size != expected or not np.all(np.isfinite(values)):
        raise SystemExit(
            f"unexpected coordinate file {path}: {values.size} values, expected {expected}"
        )
    return values, "restart_coordinate_file"


def checkpoint_files(restart_dir: Path) -> list[tuple[int, Path]]:
    result: list[tuple[int, Path]] = []
    for path in restart_dir.glob("lustre_*.dat"):
        match = STEP_PATTERN.fullmatch(path.name)
        if match:
            result.append((int(match.group(1)), path))
    result.sort()
    if not result:
        raise SystemExit(f"no lustre_<step>.dat checkpoints in {restart_dir}")
    return result


def classify_location(x: float, y: float, h: float) -> str:
    radius = math.hypot(x, y)
    distance_cells = (radius - RADIUS) / h
    if distance_cells < -4.0:
        return "SOLID_INTERIOR"
    if abs(distance_cells) <= 4.0:
        return "IBM_WALL_BAND"
    if 4.0 < distance_cells <= 12.0:
        return "NEAR_WALL_FLUID"
    if x > RADIUS and abs(y) <= 1.5:
        return "FLUID_WAKE"
    if x < -RADIUS:
        return "UPSTREAM_OR_SHOCK"
    return "FLUID_OTHER"


def inspect_checkpoint(
    step: int,
    path: Path,
    nx: int,
    ny: int,
    x: np.ndarray,
    y: np.ndarray,
    dt: float,
    reynolds: float,
    mach: float,
) -> dict[str, object]:
    cells = nx * ny
    expected_bytes = 5 * cells * np.dtype("<f8").itemsize
    actual_bytes = path.stat().st_size
    if actual_bytes != expected_bytes:
        raise SystemExit(
            f"checkpoint size mismatch for {path}: {actual_bytes}, expected {expected_bytes}"
        )

    state = np.memmap(path, dtype="<f8", mode="r", shape=(5, ny, nx))
    rho = state[0]
    mom_x = state[1]
    mom_y = state[2]
    energy = state[3]

    finite = np.isfinite(rho)
    positive = finite & (rho > 0.0)
    nonfinite_count = int(rho.size - np.count_nonzero(finite))
    nonpositive_count = int(np.count_nonzero(finite & (rho <= 0.0)))

    x_grid, y_grid = np.meshgrid(x, y)
    h = min(float(np.min(np.diff(x))), float(np.min(np.diff(y))))
    geometric_fluid = np.hypot(x_grid, y_grid) >= RADIUS + 4.0 * h
    valid_fluid = positive & geometric_fluid
    if not np.any(positive):
        raise SystemExit(f"checkpoint has no finite positive density: {path}")
    if not np.any(valid_fluid):
        raise SystemExit(f"checkpoint has no finite positive geometric-fluid density: {path}")

    safe_rho = np.where(positive, rho, np.nan)
    kinetic = 0.5 * (mom_x * mom_x + mom_y * mom_y) / safe_rho
    pressure = (GAMMA - 1.0) * (energy - kinetic)

    global_flat = int(np.nanargmin(safe_rho))
    global_iy, global_ix = np.unravel_index(global_flat, rho.shape)
    fluid_values = np.where(valid_fluid, rho, np.nan)
    fluid_flat = int(np.nanargmin(fluid_values))
    fluid_iy, fluid_ix = np.unravel_index(fluid_flat, rho.shape)

    gx = float(x[global_ix])
    gy = float(y[global_iy])
    fx = float(x[fluid_ix])
    fy = float(y[fluid_iy])
    min_rho = float(rho[global_iy, global_ix])
    fluid_min_rho = float(rho[fluid_iy, fluid_ix])
    inverse_mu = reynolds / mach
    predicted_vcfl = dt / (inverse_mu * min_rho * h * h)

    return {
        "step": step,
        "time": step * dt,
        "bytes": actual_bytes,
        "rho_min": min_rho,
        "rho_min_x": gx,
        "rho_min_y": gy,
        "rho_min_surface_distance_cells": (math.hypot(gx, gy) - RADIUS) / h,
        "rho_min_region": classify_location(gx, gy, h),
        "rho_fluid_min": fluid_min_rho,
        "rho_fluid_min_x": fx,
        "rho_fluid_min_y": fy,
        "rho_fluid_min_surface_distance_cells": (math.hypot(fx, fy) - RADIUS) / h,
        "rho_fluid_min_region": classify_location(fx, fy, h),
        "pressure_at_rho_min": float(pressure[global_iy, global_ix]),
        "p_over_rho_at_rho_min": float(pressure[global_iy, global_ix] / min_rho),
        "density_implied_vcfl": float(predicted_vcfl),
        "rho_nonfinite_count": nonfinite_count,
        "rho_nonpositive_count": nonpositive_count,
    }


def read_runtime(path: Path | None) -> dict[str, object]:
    rows: list[dict[str, float | int]] = []
    if path is None or not path.is_file():
        return {"available": False, "rows": 0}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 6 or not fields[0].isdigit():
            continue
        try:
            row = {
                "step": int(fields[0]),
                "dt": float(fields[1]),
                "time": float(fields[2]),
                "icfl": float(fields[3]),
                "vcfl": float(fields[4]),
                "cell_reynolds_min": float(fields[5]),
            }
        except ValueError:
            continue
        rows.append(row)
    if not rows:
        return {"available": True, "rows": 0}
    return {
        "available": True,
        "rows": len(rows),
        "first": rows[0],
        "last": rows[-1],
        "max_icfl": max(float(row["icfl"]) for row in rows),
        "max_vcfl": max(float(row["vcfl"]) for row in rows),
    }


def main() -> None:
    args = parse_args()
    validate_args(args)
    nx, ny, x_beg, x_end, y_beg, y_end = GRIDS[args.grid]
    fallback_x = np.linspace(x_beg + 0.5 * (x_end - x_beg) / nx,
                             x_end - 0.5 * (x_end - x_beg) / nx, nx)
    fallback_y = np.linspace(y_beg + 0.5 * (y_end - y_beg) / ny,
                             y_end - 0.5 * (y_end - y_beg) / ny, ny)
    x, x_source = load_axis(args.restart_dir / "lustre_x_cb.dat", nx, fallback_x)
    y, y_source = load_axis(args.restart_dir / "lustre_y_cb.dat", ny, fallback_y)

    rows = [
        inspect_checkpoint(step, path, nx, ny, x, y, args.dt, args.reynolds, args.mach)
        for step, path in checkpoint_files(args.restart_dir)
    ]
    args.output_tsv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    runtime = read_runtime(args.run_time_info)
    last_step = int(rows[-1]["step"])
    complete = args.expected_final_step is None or last_step >= args.expected_final_step
    density_finite_positive = all(
        int(row["rho_nonfinite_count"]) == 0
        and int(row["rho_nonpositive_count"]) == 0
        and float(row["rho_min"]) > 0.0
        for row in rows
    )
    runtime_vcfl_below_one = (
        bool(runtime.get("available"))
        and int(runtime.get("rows", 0)) > 0
        and float(runtime.get("max_vcfl", math.inf)) < 1.0
    )
    status = (
        "COMPLETED_NUMERICAL_GATE"
        if complete and density_finite_positive and runtime_vcfl_below_one
        else "INCOMPLETE_OR_UNSTABLE"
    )
    worst = min(rows, key=lambda row: float(row["rho_min"]))
    summary = {
        "diagnostic_status": status,
        "scope": "numerical-stability audit only; not physical validation or vortex ground truth",
        "restart_dir": str(args.restart_dir.resolve()),
        "grid": args.grid,
        "shape": [ny, nx],
        "coordinate_source": {"x": x_source, "y": y_source},
        "dt": args.dt,
        "mach": args.mach,
        "reynolds": args.reynolds,
        "checkpoint_count": len(rows),
        "first_step": int(rows[0]["step"]),
        "last_step": last_step,
        "expected_final_step": args.expected_final_step,
        "complete_through_expected_final_step": complete,
        "density_finite_positive": density_finite_positive,
        "runtime_vcfl_below_one": runtime_vcfl_below_one,
        "worst_checkpoint": worst,
        "final_checkpoint": rows[-1],
        "run_time_info": runtime,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
