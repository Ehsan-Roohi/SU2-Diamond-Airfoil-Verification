#!/usr/bin/env python3
"""Extract auditable shock-ridge, symmetry, and y+ metrics from SU2 CSV output.

The shock angle is measured from the *native* restart point cloud.  Density
gradients are reconstructed by a local least-squares fit over SU2 cell
neighbors; no Gaussian image filter or raster screenshot is used.  The script
writes the selected ridge points to CSV so the fit can be inspected.

No third-party Python package is required.  The default window targets a
leading-edge shock on the diamond-airfoil cases and should be changed explicitly
for another geometry or wave.  A numerical angle is not a validation result
until an independently derived reference angle is supplied.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable


CELL_NODE_COUNT = {5: 3, 9: 4}  # SU2 triangle and quadrilateral element codes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit a leading-edge density-gradient ridge on native SU2 data."
    )
    parser.add_argument("restart", type=Path, help="ASCII restart_*.csv file")
    parser.add_argument("--mesh", type=Path, required=True, help="matching SU2 mesh")
    parser.add_argument("--branch", choices=("upper", "lower"), default="upper")
    parser.add_argument("--skip-shock", action="store_true", help="only compute optional metrics")
    parser.add_argument("--x-origin", type=float, default=0.0)
    parser.add_argument("--y-origin", type=float, default=0.0)
    parser.add_argument("--x-min", type=float, default=0.03, help="relative fit-window start")
    parser.add_argument("--x-max", type=float, default=0.10, help="relative fit-window end")
    parser.add_argument("--angle-min-deg", type=float)
    parser.add_argument("--angle-max-deg", type=float)
    parser.add_argument("--bins", type=int, default=36)
    parser.add_argument(
        "--reference-angle-deg", type=float,
        help="independent signed reference; enables shock-angle error calculation",
    )
    parser.add_argument(
        "--ridge-csv", type=Path,
        help="diagnostic ridge CSV (default: beside restart file)",
    )
    parser.add_argument(
        "--metrics-json", type=Path,
        help="merge results into this JSON (default: case_metrics.json beside restart)",
    )
    parser.add_argument(
        "--symmetry", action="store_true",
        help="compute normalized mirrored-density RMS error in the display window",
    )
    parser.add_argument("--symmetry-x-min", type=float, default=-0.2)
    parser.add_argument("--symmetry-x-max", type=float, default=1.3)
    parser.add_argument("--symmetry-y-max", type=float, default=0.65)
    parser.add_argument("--symmetry-digits", type=int, default=8)
    parser.add_argument(
        "--surface-csv", type=Path,
        help="optional surface CSV containing a Y_Plus/y+ column",
    )
    return parser.parse_args()


def compact(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def find_column(fields: Iterable[str], candidates: tuple[str, ...]) -> str | None:
    lookup = {compact(field): field for field in fields}
    for candidate in candidates:
        key = compact(candidate)
        if key in lookup:
            return lookup[key]
    for cleaned, original in lookup.items():
        if any(compact(candidate) in cleaned for candidate in candidates):
            return original
    return None


def read_restart(path: Path) -> dict[int, tuple[float, float, float]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path} has no CSV header")
        point_col = find_column(reader.fieldnames, ("PointID", "Point_ID", "ID"))
        x_col = find_column(reader.fieldnames, ("x", "Points_0", "Points:0", "CoordinateX"))
        y_col = find_column(reader.fieldnames, ("y", "Points_1", "Points:1", "CoordinateY"))
        rho_col = find_column(reader.fieldnames, ("Density", "Rho"))
        missing = [
            label
            for label, column in (
                ("point ID", point_col), ("x", x_col), ("y", y_col), ("Density", rho_col)
            )
            if column is None
        ]
        if missing:
            raise ValueError(f"{path.name} is missing columns: {', '.join(missing)}")
        points: dict[int, tuple[float, float, float]] = {}
        for row_number, row in enumerate(reader):
            try:
                point_id = int(float(row[point_col]))  # type: ignore[index]
                x = float(row[x_col])  # type: ignore[index]
                y = float(row[y_col])  # type: ignore[index]
                density = float(row[rho_col])  # type: ignore[index]
            except (KeyError, TypeError, ValueError):
                continue
            if all(math.isfinite(value) for value in (x, y, density)):
                points[point_id] = (x, y, density)
        if not points:
            raise ValueError(f"{path.name} contains no readable point data")
        return points


def candidate_ids(
    points: dict[int, tuple[float, float, float]],
    x_origin: float,
    y_origin: float,
    x_min: float,
    x_max: float,
    angle_min: float,
    angle_max: float,
) -> set[int]:
    selected: set[int] = set()
    for point_id, (x, y, _density) in points.items():
        dx = x - x_origin
        dy = y - y_origin
        if dx < x_min or dx > x_max:
            continue
        angle = math.degrees(math.atan2(dy, dx))
        if angle_min <= angle <= angle_max:
            selected.add(point_id)
    return selected


def mesh_neighbors(mesh: Path, selected: set[int]) -> dict[int, set[int]]:
    neighbors = {point_id: set() for point_id in selected}
    remaining = 0
    with mesh.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.split("%", 1)[0].strip()
            if not line:
                continue
            if remaining == 0:
                if line.upper().startswith("NELEM"):
                    remaining = int(line.split("=", 1)[1])
                continue
            tokens = line.split()
            try:
                element_type = int(tokens[0])
            except (IndexError, ValueError):
                raise ValueError(f"cannot parse an element line in {mesh.name}: {line[:80]}")
            node_count = CELL_NODE_COUNT.get(element_type)
            if node_count is not None and len(tokens) >= node_count + 1:
                nodes = [int(item) for item in tokens[1:1 + node_count]]
                for node in nodes:
                    if node in neighbors:
                        neighbors[node].update(other for other in nodes if other != node)
            remaining -= 1
            if remaining == 0:
                break
    if remaining != 0:
        raise ValueError(f"mesh ended before all NELEM records were read: {mesh}")
    return neighbors


def least_squares_gradient(
    point_id: int,
    points: dict[int, tuple[float, float, float]],
    neighbors: dict[int, set[int]],
) -> tuple[float, float, float] | None:
    x0, y0, rho0 = points[point_id]
    axx = axy = ayy = bx = by = 0.0
    used = 0
    for other in neighbors.get(point_id, ()):
        if other not in points:
            continue
        x, y, rho = points[other]
        dx = x - x0
        dy = y - y0
        drho = rho - rho0
        distance2 = dx * dx + dy * dy
        if distance2 <= 1.0e-30:
            continue
        weight = 1.0 / distance2
        axx += weight * dx * dx
        axy += weight * dx * dy
        ayy += weight * dy * dy
        bx += weight * dx * drho
        by += weight * dy * drho
        used += 1
    determinant = axx * ayy - axy * axy
    if used < 2 or abs(determinant) < 1.0e-20:
        return None
    gx = (bx * ayy - by * axy) / determinant
    gy = (by * axx - bx * axy) / determinant
    return gx, gy, math.hypot(gx, gy)


def fit_shock_ridge(
    points: dict[int, tuple[float, float, float]],
    neighbors: dict[int, set[int]],
    selected: set[int],
    x_origin: float,
    y_origin: float,
    x_min: float,
    x_max: float,
    bins: int,
) -> tuple[float, float, list[tuple[int, float, float, float]]]:
    if bins < 8:
        raise ValueError("--bins must be at least 8")
    width = (x_max - x_min) / bins
    if width <= 0.0:
        raise ValueError("--x-max must exceed --x-min")
    strongest: dict[int, tuple[int, float, float, float]] = {}
    for point_id in selected:
        gradient = least_squares_gradient(point_id, points, neighbors)
        if gradient is None:
            continue
        _gx, _gy, magnitude = gradient
        x, y, _density = points[point_id]
        dx = x - x_origin
        bin_id = min(bins - 1, max(0, int((dx - x_min) / width)))
        current = strongest.get(bin_id)
        candidate = (point_id, x, y, magnitude)
        if current is None or magnitude > current[3]:
            strongest[bin_id] = candidate
    ridge = [strongest[key] for key in sorted(strongest)]
    if len(ridge) < 8:
        raise ValueError(
            f"only {len(ridge)} populated ridge bins; widen the window or inspect the solution"
        )

    magnitudes = sorted(item[3] for item in ridge)
    median = magnitudes[len(magnitudes) // 2]
    retained = [item for item in ridge if item[3] >= 0.25 * median]
    if len(retained) < 8:
        retained = ridge

    numerator = denominator = weight_sum = residual_sum = 0.0
    for _point_id, x, y, magnitude in retained:
        dx = x - x_origin
        dy = y - y_origin
        weight = max(magnitude, 1.0e-30)
        numerator += weight * dx * dy
        denominator += weight * dx * dx
        weight_sum += weight
    if denominator <= 0.0:
        raise ValueError("shock fit is singular")
    slope = numerator / denominator
    for _point_id, x, y, magnitude in retained:
        dx = x - x_origin
        dy = y - y_origin
        weight = max(magnitude, 1.0e-30)
        residual_sum += weight * (dy - slope * dx) ** 2
    rms = math.sqrt(residual_sum / max(weight_sum, 1.0e-30))
    return math.degrees(math.atan(slope)), rms, retained


def symmetry_error(
    points: dict[int, tuple[float, float, float]],
    x_min: float,
    x_max: float,
    y_max: float,
    digits: int,
) -> tuple[float, int, float]:
    lookup: dict[tuple[float, float], float] = {}
    for x, y, density in points.values():
        if x_min <= x <= x_max and abs(y) <= y_max:
            lookup[(round(x, digits), round(y, digits))] = density
    squared = 0.0
    reference_squared = 0.0
    pairs = 0
    unmatched = 0
    for (x, y), density in lookup.items():
        if y <= 10.0 ** (-digits):
            continue
        mirror = lookup.get((x, round(-y, digits)))
        if mirror is None:
            unmatched += 1
            continue
        squared += (density - mirror) ** 2
        reference_squared += 0.25 * (abs(density) + abs(mirror)) ** 2
        pairs += 1
    if pairs == 0 or reference_squared <= 0.0:
        raise ValueError("no mirrored point pairs were found for the symmetry metric")
    normalized_rms = math.sqrt(squared / reference_squared)
    unmatched_fraction = unmatched / max(pairs + unmatched, 1)
    return normalized_rms, pairs, unmatched_fraction


def yplus_metrics(path: Path) -> dict[str, float | int]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path} has no CSV header")
        yplus_col = find_column(reader.fieldnames, ("Y_Plus", "YPlus", "y+"))
        if yplus_col is None:
            raise ValueError(
                f"{path.name} has no Y_Plus column; export wall y+ to CSV from ParaView"
            )
        values = []
        for row in reader:
            try:
                value = abs(float(row[yplus_col]))
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(value):
                values.append(value)
    if not values:
        raise ValueError(f"{path.name} has no readable y+ values")
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "yplus_max": max(values),
        "yplus_mean": sum(values) / len(values),
        "yplus_p95": ordered[p95_index],
        "yplus_samples": len(values),
    }


def main() -> int:
    args = parse_args()
    restart = args.restart.resolve()
    mesh = args.mesh.resolve()
    if not restart.is_file():
        raise SystemExit(f"Restart CSV not found: {restart}")
    if not mesh.is_file():
        raise SystemExit(f"Mesh not found: {mesh}")

    metrics_path = (
        args.metrics_json.resolve()
        if args.metrics_json is not None
        else restart.parent / "case_metrics.json"
    )
    metrics: dict[str, Any] = {}
    if metrics_path.exists():
        try:
            existing = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Cannot read existing metrics JSON: {exc}")
        if isinstance(existing, dict):
            metrics.update(existing)

    try:
        points = read_restart(restart)
        if not args.skip_shock:
            if args.angle_min_deg is None or args.angle_max_deg is None:
                angle_min, angle_max = (
                    (12.0, 60.0) if args.branch == "upper" else (-60.0, -12.0)
                )
            else:
                angle_min, angle_max = args.angle_min_deg, args.angle_max_deg
            if angle_max <= angle_min:
                raise ValueError("angle maximum must exceed angle minimum")
            selected = candidate_ids(
                points,
                args.x_origin,
                args.y_origin,
                args.x_min,
                args.x_max,
                angle_min,
                angle_max,
            )
            if not selected:
                raise ValueError("no restart points lie in the requested shock window")
            neighbors = mesh_neighbors(mesh, selected)
            angle, fit_rms, ridge = fit_shock_ridge(
                points,
                neighbors,
                selected,
                args.x_origin,
                args.y_origin,
                args.x_min,
                args.x_max,
                args.bins,
            )
            ridge_path = (
                args.ridge_csv.resolve()
                if args.ridge_csv is not None
                else restart.parent / f"shock_ridge_{args.branch}.csv"
            )
            with ridge_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(("PointID", "x", "y", "density_gradient_magnitude"))
                writer.writerows(ridge)
            angles = metrics.setdefault("shock_angles_deg", {})
            if not isinstance(angles, dict):
                angles = {}
                metrics["shock_angles_deg"] = angles
            angles[args.branch] = angle
            fit_errors = metrics.setdefault("shock_fit_rms", {})
            if not isinstance(fit_errors, dict):
                fit_errors = {}
                metrics["shock_fit_rms"] = fit_errors
            fit_errors[args.branch] = fit_rms
            extraction = metrics.setdefault("shock_extraction", {})
            if not isinstance(extraction, dict):
                extraction = {}
                metrics["shock_extraction"] = extraction
            extraction[args.branch] = {
                "source": str(restart),
                "mesh": str(mesh),
                "method": "native-point least-squares density gradient; binned ridge; weighted line through origin",
                "display_filter_used": False,
                "fit_window_x_over_c": [args.x_min, args.x_max],
                "angle_window_deg": [angle_min, angle_max],
                "ridge_csv": str(ridge_path),
            }
            if args.reference_angle_deg is not None:
                errors = metrics.setdefault("shock_angle_errors_deg", {})
                if not isinstance(errors, dict):
                    errors = {}
                    metrics["shock_angle_errors_deg"] = errors
                errors[args.branch] = abs(angle - args.reference_angle_deg)
                metrics["shock_angle_error_deg"] = max(float(value) for value in errors.values())

        if args.symmetry:
            error, pairs, unmatched_fraction = symmetry_error(
                points,
                args.symmetry_x_min,
                args.symmetry_x_max,
                args.symmetry_y_max,
                args.symmetry_digits,
            )
            metrics["symmetry_error_rms"] = error
            metrics["symmetry_pairs"] = pairs
            metrics["symmetry_unmatched_fraction"] = unmatched_fraction
            metrics["symmetry_definition"] = (
                "sqrt(sum((rho(x,y)-rho(x,-y))^2)/"
                "sum(((abs(rho(x,y))+abs(rho(x,-y)))/2)^2))"
            )

        if args.surface_csv is not None:
            metrics.update(yplus_metrics(args.surface_csv.resolve()))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Metric extraction failed: {exc}")

    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(f"Metrics: {metrics_path}")
    if not args.skip_shock:
        print(f"{args.branch} fitted shock angle: {metrics['shock_angles_deg'][args.branch]:.6f} deg")
        if args.reference_angle_deg is None:
            print("No reference angle supplied; no shock-angle validation error was claimed.")
        else:
            print(f"Absolute angle error: {metrics['shock_angle_errors_deg'][args.branch]:.6f} deg")
    if args.symmetry:
        print(f"Normalized mirrored-density RMS error: {metrics['symmetry_error_rms']:.6g}")
    if args.surface_csv is not None:
        print(f"Maximum y+: {metrics['yplus_max']:.6g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
