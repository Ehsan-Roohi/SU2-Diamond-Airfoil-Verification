#!/usr/bin/env python3
"""Make a compact, uploadable near-field package from partitioned MFC Silo files."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import h5py
import numpy as np


NX, NY = 2970, 2700
X_BEG, X_END = -5.0, 6.0
Y_BEG, Y_END = -5.0, 5.0
DT = 1.0 / 5400.0
FIELD_NAMES = ("rho", "pres", "vel1", "vel2")


def decode(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def read_quadvar(handle: h5py.File, name: str) -> np.ndarray:
    meta = handle[name].attrs["silo"]
    raw = np.asarray(handle[decode(meta["value0"])][...])
    return raw.ravel(order="C").reshape(raw.shape, order="F")


def read_point(handle: h5py.File, name: str) -> float:
    if name not in handle:
        return math.nan
    meta = handle[name].attrs["silo"]
    return float(handle[decode(meta["data0"])][0])


def partition_layout(handle: h5py.File) -> tuple[np.ndarray, np.ndarray, slice, slice]:
    meta = handle["rectilinear_grid"].attrs["silo"]
    xnode = np.asarray(handle[decode(meta["coord0"])][...])
    ynode = np.asarray(handle[decode(meta["coord1"])][...])
    i0, j0 = map(int, meta["min_index"][:2])
    i1, j1 = map(int, meta["max_index"][:2])
    xc = 0.5 * (xnode[i0:i1] + xnode[i0 + 1 : i1 + 1])
    yc = 0.5 * (ynode[j0:j1] + ynode[j0 + 1 : j1 + 1])
    dx = (X_END - X_BEG) / NX
    dy = (Y_END - Y_BEG) / NY
    ix = np.rint((xc - X_BEG) / dx - 0.5).astype(int)
    iy = np.rint((yc - Y_BEG) / dy - 0.5).astype(int)
    return ix, iy, slice(i0, i1), slice(j0, j1)


def matched_positions(global_indices: np.ndarray, selected: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    destination = np.searchsorted(selected, global_indices)
    valid = destination < selected.size
    valid[valid] &= selected[destination[valid]] == global_indices[valid]
    return np.flatnonzero(valid), destination[valid]


def numeric_steps(part0: Path) -> list[int]:
    steps = sorted(int(path.stem) for path in part0.glob("*.silo") if path.stem.isdigit())
    if not steps:
        raise RuntimeError(f"No numeric .silo snapshots found in {part0}")
    return steps


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--xmin", type=float, default=-1.2)
    parser.add_argument("--xmax", type=float, default=4.5)
    parser.add_argument("--ymin", type=float, default=-1.5)
    parser.add_argument("--ymax", type=float, default=3.5)
    parser.add_argument("--stride", type=int, default=3)
    parser.add_argument(
        "--steps",
        default=None,
        help="optional comma-separated subset, useful for a quick validation",
    )
    args = parser.parse_args()

    if args.stride <= 0:
        parser.error("--stride must be positive")
    run_dir = args.run_dir.resolve()
    silo = run_dir / "silo_hdf5"
    parts = sorted(
        (path for path in silo.glob("p*") if path.is_dir() and path.name[1:].isdigit()),
        key=lambda path: int(path.name[1:]),
    )
    if not parts:
        raise RuntimeError(f"No Silo partitions found below {silo}")

    steps = numeric_steps(parts[0])
    if args.steps:
        requested = [int(value) for value in args.steps.split(",")]
        missing = sorted(set(requested) - set(steps))
        if missing:
            raise RuntimeError(f"Requested snapshots not found: {missing}")
        steps = requested

    dx = (X_END - X_BEG) / NX
    dy = (Y_END - Y_BEG) / NY
    x_full = X_BEG + (np.arange(NX) + 0.5) * dx
    y_full = Y_BEG + (np.arange(NY) + 0.5) * dy
    ix_selected = np.flatnonzero((x_full >= args.xmin) & (x_full <= args.xmax))[:: args.stride]
    iy_selected = np.flatnonzero((y_full >= args.ymin) & (y_full <= args.ymax))[:: args.stride]
    x = x_full[ix_selected].astype(np.float32)
    y = y_full[iy_selected].astype(np.float32)

    shape = (len(steps), len(x), len(y))
    fields = {name: np.empty(shape, dtype=np.float32) for name in FIELD_NAMES}
    ib_mask = np.empty(shape, dtype=np.uint8)
    force_x = np.full(len(steps), np.nan, dtype=np.float64)
    force_y = np.full(len(steps), np.nan, dtype=np.float64)

    for it, step in enumerate(steps):
        coverage = np.zeros((len(x), len(y)), dtype=np.uint8)
        for ip, part in enumerate(parts):
            path = part / f"{step}.silo"
            if not path.is_file():
                raise RuntimeError(f"Missing partition snapshot: {path}")
            with h5py.File(path, "r") as handle:
                ix, iy, sx, sy = partition_layout(handle)
                local_x, target_x = matched_positions(ix, ix_selected)
                local_y, target_y = matched_positions(iy, iy_selected)
                if not local_x.size or not local_y.size:
                    continue
                source = np.ix_(local_x, local_y)
                target = np.ix_(target_x, target_y)
                for name in FIELD_NAMES:
                    core = read_quadvar(handle, name)[sx, sy]
                    fields[name][it][target] = core[source].astype(np.float32, copy=False)
                ib_core = read_quadvar(handle, "ib_markers")[sx, sy]
                ib_mask[it][target] = (ib_core[source] > 0.5).astype(np.uint8)
                coverage[target] += 1
                if ip == 0:
                    force_x[it] = read_point(handle, "ib_force_x")
                    force_y[it] = read_point(handle, "ib_force_y")
        if coverage.min() != 1 or coverage.max() != 1:
            raise RuntimeError(
                f"Invalid cropped partition coverage for step {step}: "
                f"{coverage.min()}..{coverage.max()}"
            )
        print(f"packed {it + 1:3d}/{len(steps)}: step={step}, t={step * DT:.5f}", flush=True)

    output = args.output or (run_dir / "MFC_A40_STARTUP_COMPACT.npz")
    output = output.resolve()
    metadata = {
        "description": "MFC Mach-3, alpha=40 deg, Euler/IBM f270 startup near-field",
        "full_grid": [NX, NY],
        "domain": [X_BEG, X_END, Y_BEG, Y_END],
        "crop_requested": [args.xmin, args.xmax, args.ymin, args.ymax],
        "stride": args.stride,
        "partitions": len(parts),
        "dt": DT,
        "fields": ["rho", "pres", "vel1", "vel2", "ib_mask"],
        "note": "Schlieren and vorticity are derived from rho,u,v; raw Silo stays on Unity.",
    }
    np.savez_compressed(
        output,
        x=x,
        y=y,
        steps=np.asarray(steps, dtype=np.int32),
        time=np.asarray(steps, dtype=np.float64) * DT,
        rho=fields["rho"],
        pres=fields["pres"],
        vel1=fields["vel1"],
        vel2=fields["vel2"],
        ib_mask=ib_mask,
        ib_force_x=force_x,
        ib_force_y=force_y,
        metadata_json=np.asarray(json.dumps(metadata, indent=2)),
    )
    print(f"output={output}")
    print(f"bytes={output.stat().st_size}")


if __name__ == "__main__":
    main()
