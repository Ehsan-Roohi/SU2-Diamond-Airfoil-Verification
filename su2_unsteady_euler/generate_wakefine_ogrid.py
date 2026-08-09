#!/usr/bin/env python3
"""Generate a sharp-diamond O-grid refined along the downstream wake ray.

The topology is the same body-fitted O-grid used by the steady Euler teaching
case, but both circumferential and radial resolution are doubled relative to
the 720 x 181 mesh.  Increasing NJ to 721 is deliberate: merely doubling NJ
would leave the downstream spacing near x/c=2 substantially coarser than the
MFC-medium comparison grid.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ni", type=int, default=1440)
    parser.add_argument("--nj", type=int, default=721)
    parser.add_argument("--epsilon-deg", type=float, default=8.0)
    parser.add_argument("--far-radius", type=float, default=20.0)
    parser.add_argument("--first-cell", type=float, default=1.5e-4)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("mesh/diamond_euler_sharp_wakefine_1440x721.su2"),
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="build and validate coordinates without writing the SU2 mesh",
    )
    return parser.parse_args()


def stretching_parameter(nj: int, target_ratio: float) -> float:
    """Solve sinh(a/(nj-1))/sinh(a)=target_ratio by bisection."""
    def residual(a: float) -> float:
        return math.sinh(a / (nj - 1)) / math.sinh(a) - target_ratio

    lo, hi = 1.0e-10, 40.0
    if residual(lo) * residual(hi) >= 0.0:
        raise ValueError("requested first-cell spacing has no stretching root")
    for _ in range(120):
        mid = 0.5 * (lo + hi)
        if residual(lo) * residual(mid) <= 0.0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def signed_cell_areas(xy: np.ndarray) -> np.ndarray:
    q0 = xy[:-1]
    q1 = xy[1:]
    q2 = np.roll(xy[1:], -1, axis=1)
    q3 = np.roll(xy[:-1], -1, axis=1)
    x = np.stack((q0[..., 0], q1[..., 0], q2[..., 0], q3[..., 0]), axis=-1)
    y = np.stack((q0[..., 1], q1[..., 1], q2[..., 1], q3[..., 1]), axis=-1)
    return 0.5 * np.sum(x * np.roll(y, -1, axis=-1) - y * np.roll(x, -1, axis=-1), axis=-1)


def build_coordinates(args: argparse.Namespace) -> tuple[np.ndarray, float]:
    if args.ni < 16 or args.ni % 4:
        raise ValueError("NI must be at least 16 and divisible by four")
    if args.nj < 3:
        raise ValueError("NJ must be at least three")
    if args.far_radius <= 1.0:
        raise ValueError("far radius must exceed one chord")
    if args.first_cell <= 0.0:
        raise ValueError("first-cell spacing must be positive")

    half_thickness = 0.5 * math.tan(math.radians(args.epsilon_deg))
    vertices = np.array(
        [[0.0, 0.0], [0.5, -half_thickness], [1.0, 0.0], [0.5, half_thickness]],
        dtype=float,
    )
    per_panel = args.ni // 4
    wall = np.empty((args.ni, 2), dtype=float)
    cursor = 0
    for panel in range(4):
        p0, p1 = vertices[panel], vertices[(panel + 1) % 4]
        for local in range(per_panel):
            t = local / per_panel
            wall[cursor] = (1.0 - t) * p0 + t * p1
            cursor += 1

    centre = np.array([0.5, 0.0])
    ray = wall - centre
    ray /= np.linalg.norm(ray, axis=1)[:, None]
    far = centre + args.far_radius * ray
    lengths = np.linalg.norm(far - wall, axis=1)
    target_ratio = args.first_cell / float(lengths.mean())
    stretch = stretching_parameter(args.nj, target_ratio)
    eta = np.arange(args.nj, dtype=float) / (args.nj - 1)
    s = np.sinh(stretch * eta) / math.sinh(stretch)
    xy = wall[None, :, :] + s[:, None, None] * (far - wall)[None, :, :]
    return xy, stretch


def report_quality(xy: np.ndarray, stretch: float) -> None:
    areas = signed_cell_areas(xy)
    first = np.linalg.norm(xy[1] - xy[0], axis=1)
    te_index = xy.shape[1] // 2
    wake_x = xy[:, te_index, 0]
    print(f"grid_points={xy.shape[0] * xy.shape[1]}")
    print(f"grid_cells={(xy.shape[0] - 1) * xy.shape[1]}")
    print(f"stretch_parameter={stretch:.12g}")
    print(f"min_signed_area={areas.min():.12e}")
    print(f"max_signed_area={areas.max():.12e}")
    print(f"nonpositive_cells={np.count_nonzero(areas <= 0.0)}")
    print(
        "first_cell_min_mean_max="
        f"{first.min():.12e},{first.mean():.12e},{first.max():.12e}"
    )
    for target in (1.1, 1.5, 2.0, 3.0, 5.0):
        idx = int(np.argmin(np.abs(wake_x - target)))
        if 0 < idx < len(wake_x) - 1:
            spacing = 0.5 * (wake_x[idx + 1] - wake_x[idx - 1])
            print(
                f"wake_dx_at_x{target:g}={spacing:.12e} "
                f"sample_x={wake_x[idx]:.12e}"
            )
    if np.any(areas <= 0.0):
        raise RuntimeError("mesh contains nonpositive cells")


def write_su2(path: Path, xy: np.ndarray) -> None:
    nj, ni, _ = xy.shape
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="ascii", newline="\n") as stream:
        stream.write("NDIME= 2\n")
        stream.write(f"NPOIN= {ni * nj}\n")
        for j in range(nj):
            for i in range(ni):
                idx = j * ni + i
                stream.write(f"{xy[j, i, 0]:.16e} {xy[j, i, 1]:.16e} {idx}\n")
        stream.write(f"NELEM= {ni * (nj - 1)}\n")
        eid = 0
        for j in range(nj - 1):
            for i in range(ni):
                ip = (i + 1) % ni
                n0 = j * ni + i
                n1 = (j + 1) * ni + i
                n2 = (j + 1) * ni + ip
                n3 = j * ni + ip
                stream.write(f"9 {n0} {n1} {n2} {n3} {eid}\n")
                eid += 1
        stream.write("NMARK= 2\n")
        stream.write("MARKER_TAG= airfoil\n")
        stream.write(f"MARKER_ELEMS= {ni}\n")
        for i in range(ni):
            stream.write(f"3 {i} {(i + 1) % ni}\n")
        stream.write("MARKER_TAG= farfield\n")
        stream.write(f"MARKER_ELEMS= {ni}\n")
        base = (nj - 1) * ni
        for i in range(ni):
            stream.write(f"3 {base + (i + 1) % ni} {base + i}\n")
    print(f"mesh_file={path.resolve()}")
    print(f"mesh_bytes={path.stat().st_size}")


def main() -> int:
    args = parse_args()
    xy, stretch = build_coordinates(args)
    report_quality(xy, stretch)
    if not args.validate_only:
        write_su2(args.output, xy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
