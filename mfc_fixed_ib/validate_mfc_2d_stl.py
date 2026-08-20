#!/usr/bin/env python3
"""Reject STL inputs that collapse to zero boundary edges in MFC's 2-D IB."""

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sys


parser = argparse.ArgumentParser()
parser.add_argument("stl", type=Path)
parser.add_argument("--expected-edges", type=int, default=4)
parser.add_argument("--tol", type=float, default=1.0e-12)
args = parser.parse_args()

vertices = []
for line_number, line in enumerate(args.stl.read_text().splitlines(), start=1):
    fields = line.split()
    if fields and fields[0].lower() == "vertex":
        if len(fields) != 4:
            raise SystemExit(f"invalid vertex at line {line_number}")
        vertices.append(tuple(float(value) for value in fields[1:]))

if not vertices or len(vertices) % 3:
    raise SystemExit("STL must contain complete ASCII triangle facets")


def xy_key(vertex):
    return tuple(round(value / args.tol) for value in vertex[:2])


triangles = [vertices[index : index + 3] for index in range(0, len(vertices), 3)]
edge_counts = Counter()
projected_area = 0.0
for triangle in triangles:
    a, b, c = triangle
    projected_area += abs(
        (b[0] - a[0]) * (c[1] - a[1])
        - (b[1] - a[1]) * (c[0] - a[0])
    ) / 2.0
    keys = [xy_key(vertex) for vertex in triangle]
    for first, second in ((keys[0], keys[1]), (keys[1], keys[2]), (keys[2], keys[0])):
        edge_counts[tuple(sorted((first, second)))] += 1

boundary_edges = [edge for edge, count in edge_counts.items() if count == 1]
nonmanifold_edges = [edge for edge, count in edge_counts.items() if count > 2]
degree = defaultdict(int)
for first, second in boundary_edges:
    degree[first] += 1
    degree[second] += 1

max_abs_z = max(abs(vertex[2]) for vertex in vertices)
failures = []
if len(boundary_edges) != args.expected_edges:
    failures.append(
        f"expected {args.expected_edges} projected boundary edges, found {len(boundary_edges)}"
    )
if max_abs_z > args.tol:
    failures.append(f"STL is not planar at z=0: max |z|={max_abs_z:.6g}")
if projected_area <= args.tol:
    failures.append("projected area is zero")
if nonmanifold_edges:
    failures.append(f"found {len(nonmanifold_edges)} non-manifold projected edges")
if any(value != 2 for value in degree.values()):
    failures.append("projected boundary is not a closed single-degree-2 loop")

print(f"stl={args.stl}")
print(f"triangles={len(triangles)}")
print(f"projected_boundary_edges={len(boundary_edges)}")
print(f"projected_area={projected_area:.10f}")
print(f"max_abs_z={max_abs_z:.6g}")

if failures:
    print("status=FAIL")
    for failure in failures:
        print(f"error={failure}", file=sys.stderr)
    raise SystemExit(2)

print("status=PASS")
