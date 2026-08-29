#!/usr/bin/env python3
"""Render close-up scalar fields from an SU2 VTU file.

This utility is deliberately separate from DART: it converts the native SU2
field into a clean raster image suitable for an open-vocabulary detector.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.tri as mtri
import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy


DEFAULT_FIELDS = ("Mach", "Pressure", "Density", "Eddy_Viscosity")


def read_grid(path: Path):
    reader = vtk.vtkXMLUnstructuredGridReader()
    reader.SetFileName(str(path))
    reader.Update()
    grid = reader.GetOutput()
    if grid.GetNumberOfPoints() == 0 or grid.GetNumberOfCells() == 0:
        raise ValueError(f"empty or unreadable VTU grid: {path}")
    return grid


def triangulation(grid) -> tuple[mtri.Triangulation, np.ndarray]:
    points = vtk_to_numpy(grid.GetPoints().GetData())
    offsets = vtk_to_numpy(grid.GetCells().GetOffsetsArray())
    connectivity = vtk_to_numpy(grid.GetCells().GetConnectivityArray())
    widths = np.diff(offsets)
    if not np.all(widths == 4):
        raise ValueError("this pilot renderer expects an all-quad SU2 grid")
    quads = connectivity.reshape(-1, 4)
    triangles = np.vstack((quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]))
    return mtri.Triangulation(points[:, 0], points[:, 1], triangles), points


def scalar(grid, name: str) -> np.ndarray:
    array = grid.GetPointData().GetArray(name)
    if array is None:
        available = [
            grid.GetPointData().GetArrayName(i)
            for i in range(grid.GetPointData().GetNumberOfArrays())
        ]
        raise KeyError(f"missing point field {name!r}; available={available}")
    values = vtk_to_numpy(array)
    if values.ndim != 1:
        raise ValueError(f"field {name!r} is not scalar: shape={values.shape}")
    return values


def limits(values: np.ndarray) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    return tuple(float(x) for x in np.percentile(finite, (0.5, 99.5)))


def draw_airfoil(ax) -> None:
    x = np.array((0.0, 0.5, 1.0, 0.5, 0.0))
    y = np.array((0.0, 0.08, 0.0, -0.08, 0.0))
    ax.fill(x, y, color="black", edgecolor="white", linewidth=0.8, zorder=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("vtu", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--field", action="append", dest="fields")
    parser.add_argument("--xmin", type=float, default=-1.0)
    parser.add_argument("--xmax", type=float, default=3.2)
    parser.add_argument("--ymin", type=float, default=-1.8)
    parser.add_argument("--ymax", type=float, default=1.8)
    args = parser.parse_args()

    fields = tuple(args.fields or DEFAULT_FIELDS)
    if len(fields) not in (1, 4):
        parser.error("use exactly one --field or exactly four --field values")

    grid = read_grid(args.vtu)
    tri, _ = triangulation(grid)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    if len(fields) == 1:
        fig, axis = plt.subplots(figsize=(12, 7), constrained_layout=True)
        axes = [axis]
    else:
        fig, grid_axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
        axes = list(grid_axes.flat)

    cmaps = {
        "Mach": "turbo",
        "Pressure": "cividis",
        "Density": "viridis",
        "Eddy_Viscosity": "magma",
    }
    for ax, field in zip(axes, fields):
        values = scalar(grid, field)
        vmin, vmax = limits(values)
        artist = ax.tripcolor(
            tri,
            values,
            shading="gouraud",
            cmap=cmaps.get(field, "viridis"),
            vmin=vmin,
            vmax=vmax,
            rasterized=True,
        )
        draw_airfoil(ax)
        ax.set(
            title=field.replace("_", " "),
            xlabel=r"$x/c$",
            ylabel=r"$y/c$",
            xlim=(args.xmin, args.xmax),
            ylim=(args.ymin, args.ymax),
            aspect="equal",
        )
        fig.colorbar(artist, ax=ax, shrink=0.82)

    fig.suptitle(r"SU2/SST: $M_\infty=3$, $\alpha=40^\circ$, $Re_c=10^6$")
    fig.savefig(args.output, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
