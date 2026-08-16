#!/usr/bin/env python3
"""Pack the completed f405 MFC result into a compact movie-ready NPZ."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import h5py


NX = 4455
NY = 4050
X_BEG, X_END = -5.0, 6.0
Y_BEG, Y_END = -5.0, 5.0
DT = 1.0 / 8100.0
SAVE_EVERY = 4374
FINAL_STEP = 109350
EXPECTED_STEPS = list(range(0, FINAL_STEP + 1, SAVE_EVERY))
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


def matched_positions(
    global_indices: np.ndarray, selected: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    destination = np.searchsorted(selected, global_indices)
    valid = destination < selected.size
    valid[valid] &= selected[destination[valid]] == global_indices[valid]
    return np.flatnonzero(valid), destination[valid]


def numeric_steps(part0: Path) -> list[int]:
    return sorted(int(path.stem) for path in part0.glob("*.silo") if path.stem.isdigit())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--xmin", type=float, default=-1.5)
    parser.add_argument("--xmax", type=float, default=5.5)
    parser.add_argument("--ymin", type=float, default=-2.5)
    parser.add_argument("--ymax", type=float, default=4.5)
    parser.add_argument("--stride", type=int, default=6)
    args = parser.parse_args()

    global h5py
    import h5py

    if args.stride <= 0:
        parser.error("--stride must be positive")
    if not (X_BEG <= args.xmin < args.xmax <= X_END):
        parser.error("x crop must lie inside the f405 domain")
    if not (Y_BEG <= args.ymin < args.ymax <= Y_END):
        parser.error("y crop must lie inside the f405 domain")

    run_dir = args.run_dir.resolve()
    silo = run_dir / "silo_hdf5"
    parts = sorted(
        (path for path in silo.glob("p*") if path.is_dir() and path.name[1:].isdigit()),
        key=lambda path: int(path.name[1:]),
    )
    if not parts:
        raise RuntimeError(f"No Silo partitions found below {silo}")

    found_steps = numeric_steps(parts[0])
    missing = sorted(set(EXPECTED_STEPS) - set(found_steps))
    if missing:
        raise RuntimeError(f"Missing required f405 snapshots in p0: {missing}")
    steps = EXPECTED_STEPS

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
                # MFC writes integrated IB loads only to the rank-0 Silo slave.
                # Read them before checking crop overlap: p0 can lie completely
                # outside the requested near-field crop while still owning the
                # point-mesh force variables.
                if ip == 0:
                    force_x[it] = read_point(handle, "ib_force_x")
                    force_y[it] = read_point(handle, "ib_force_y")
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
        if coverage.min() != 1 or coverage.max() != 1:
            raise RuntimeError(
                f"Invalid cropped partition coverage for step {step}: "
                f"{coverage.min()}..{coverage.max()}"
            )
        print(
            f"packed {it + 1:2d}/{len(steps)}: step={step}, t={step * DT:.5f}",
            flush=True,
        )

    if not (np.isfinite(force_x).all() and np.isfinite(force_y).all()):
        raise RuntimeError(
            "IB force variables were not recovered from the rank-0 Silo files; "
            "refusing to create a compact package with silent NaN loads"
        )

    output = args.output.resolve()
    metadata = {
        "description": "MFC Mach-3 alpha=40 Euler/IBM f405 movie-ready near field",
        "full_grid": [NX, NY],
        "domain": [X_BEG, X_END, Y_BEG, Y_END],
        "crop_requested": [args.xmin, args.xmax, args.ymin, args.ymax],
        "packed_grid": [len(x), len(y)],
        "stride": args.stride,
        "partitions": len(parts),
        "dt": DT,
        "save_every": SAVE_EVERY,
        "fields": ["rho", "pres", "vel1", "vel2", "ib_mask"],
        "movie_note": (
            "Derive numerical Schlieren from grad(rho), vorticity from grad(u,v), "
            "and streamlines from vel1/vel2. The 26 physical snapshots are 0.54 "
            "time units apart; visual interpolation must not be described as new data."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
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
    print(f"packed_grid={len(x)}x{len(y)}")
    print(f"snapshots={len(steps)}")
    print(f"bytes={output.stat().st_size}")


if __name__ == "__main__":
    main()
