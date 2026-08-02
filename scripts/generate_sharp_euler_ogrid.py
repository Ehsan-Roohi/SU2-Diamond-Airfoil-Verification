#!/usr/bin/env python3
"""Regenerate the distributed sharp-diamond Euler O-grid.

This is deliberately separate from the SST wall-normal grid.  The diamond has
four mathematically sharp vertices; radial rays from the body centre provide a
single, non-degenerate grid line at each corner. It reproduces the geometry and
settings of the qualified teaching mesh; it is not a grid-family generator or
a production C-grid with a wake cut. Requires NumPy and SciPy.
"""

from pathlib import Path
import math
import numpy as np
from scipy.optimize import brentq


NI = 720
NJ = 181
EPS_DEG = 8.0
FAR_RADIUS = 20.0
FIRST_CELL = 3.0e-4


def signed_area(q):
    x, y = q[:, 0], q[:, 1]
    return 0.5 * np.sum(x * np.roll(y, -1) - y * np.roll(x, -1))


def main():
    h = 0.5 * math.tan(math.radians(EPS_DEG))
    vertices = np.array([[0.0, 0.0], [0.5, -h], [1.0, 0.0], [0.5, h]])
    per_panel = NI // 4
    wall = []
    for k in range(4):
        p0, p1 = vertices[k], vertices[(k + 1) % 4]
        for j in range(per_panel):
            t = j / per_panel
            wall.append((1.0 - t) * p0 + t * p1)
    wall = np.asarray(wall)

    centre = np.array([0.5, 0.0])
    ray = wall - centre
    ray /= np.linalg.norm(ray, axis=1)[:, None]
    far = centre + FAR_RADIUS * ray
    lengths = np.linalg.norm(far - wall, axis=1)
    target_ratio = FIRST_CELL / float(lengths.mean())
    f = lambda a: math.sinh(a / (NJ - 1)) / math.sinh(a) - target_ratio
    a = brentq(f, 1.0e-8, 40.0)
    eta = np.arange(NJ, dtype=float) / (NJ - 1)
    s = np.sinh(a * eta) / math.sinh(a)
    xy = wall[None, :, :] + s[:, None, None] * (far - wall)[None, :, :]

    areas = []
    for j in range(NJ - 1):
        for i in range(NI):
            ip = (i + 1) % NI
            q = np.array([xy[j, i], xy[j + 1, i], xy[j + 1, ip], xy[j, ip]])
            areas.append(signed_area(q))
    areas = np.asarray(areas)
    first = np.linalg.norm(xy[1] - xy[0], axis=1)
    print(f"min/max signed area: {areas.min():.6e} {areas.max():.6e}")
    print(f"nonpositive cells: {np.count_nonzero(areas <= 0)}")
    print(f"first-cell min/mean/max: {first.min():.6e} {first.mean():.6e} {first.max():.6e}")

    out = Path(__file__).resolve().parents[1] / "meshes" / "diamond_euler_sharp_medium_720x181.su2"
    with out.open("w", encoding="ascii") as fobj:
        fobj.write("NDIME= 2\n")
        fobj.write(f"NPOIN= {NI * NJ}\n")
        for j in range(NJ):
            for i in range(NI):
                idx = j * NI + i
                fobj.write(f"{xy[j,i,0]:.16e} {xy[j,i,1]:.16e} {idx}\n")
        fobj.write(f"NELEM= {NI * (NJ - 1)}\n")
        eid = 0
        for j in range(NJ - 1):
            for i in range(NI):
                ip = (i + 1) % NI
                n0, n1 = j * NI + i, (j + 1) * NI + i
                n2, n3 = (j + 1) * NI + ip, j * NI + ip
                fobj.write(f"9 {n0} {n1} {n2} {n3} {eid}\n")
                eid += 1
        fobj.write("NMARK= 2\nMARKER_TAG= airfoil\n")
        fobj.write(f"MARKER_ELEMS= {NI}\n")
        for i in range(NI):
            fobj.write(f"3 {i} {(i + 1) % NI}\n")
        fobj.write("MARKER_TAG= farfield\n")
        fobj.write(f"MARKER_ELEMS= {NI}\n")
        base = (NJ - 1) * NI
        for i in range(NI):
            fobj.write(f"3 {base + (i + 1) % NI} {base + i}\n")
    print(out)


if __name__ == "__main__":
    main()
