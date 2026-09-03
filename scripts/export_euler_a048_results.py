#!/usr/bin/env python3
"""Export compact, auditable results from the SU2 Mach-3 AoA 0/4/8 campaign.

Large restart and VTU files remain in the lossless Unity archive.  This exporter
creates GitHub-sized evidence: standardized histories, scalar summaries,
shock-ridge tables, provenance, and fixed-presentation comparison figures.
Numerical acceptance is never inferred from the existence of a field file.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from typing import Iterable


ANGLES = (0, 4, 8)
WINDOW = 200
GAMMA = 1.4


def compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def find_column(fields: Iterable[str], candidates: Iterable[str]) -> str | None:
    lookup = {compact(field): field for field in fields}
    for candidate in candidates:
        match = lookup.get(compact(candidate))
        if match is not None:
            return match
    return None


def finite_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def sample_std(values: list[float]) -> float | None:
    if len(values) < 2:
        return 0.0 if values else None
    center = sum(values) / len(values)
    return math.sqrt(sum((value - center) ** 2 for value in values) / (len(values) - 1))


def summarize_history(path: Path, reduced_path: Path) -> dict[str, object]:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        iteration_col = find_column(fields, ("Iteration", "Iter", "Time_Iter", "Outer_Iter"))
        residual_col = find_column(fields, ("RMS_DENSITY", "RMS[Rho]", "RMSRho"))
        cl_col = find_column(fields, ("LIFT", "CL", "C_L"))
        cd_col = find_column(fields, ("DRAG", "CD", "C_D"))
        if cl_col is None or cd_col is None:
            raise ValueError(f"{path} has no recognizable CL/CD columns")
        rows: list[dict[str, float | int | None]] = []
        for index, row in enumerate(reader):
            cl = finite_float(row.get(cl_col))
            cd = finite_float(row.get(cd_col))
            if cl is None or cd is None:
                continue
            iteration = finite_float(row.get(iteration_col)) if iteration_col else None
            residual = finite_float(row.get(residual_col)) if residual_col else None
            rows.append(
                {
                    "iteration": int(iteration) if iteration is not None else index,
                    "density_residual_log10": residual,
                    "cl": cl,
                    "cd": cd,
                }
            )
    if not rows:
        raise ValueError(f"{path} has no finite aerodynamic records")
    reduced_path.parent.mkdir(parents=True, exist_ok=True)
    with reduced_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("iteration", "density_residual_log10", "cl", "cd"),
        )
        writer.writeheader()
        writer.writerows(rows)
    tail = rows[-min(WINDOW, len(rows)) :]
    cls = [float(row["cl"]) for row in tail]
    cds = [float(row["cd"]) for row in tail]
    residuals = [
        float(row["density_residual_log10"])
        for row in rows
        if row["density_residual_log10"] is not None
    ]
    return {
        "history_rows": len(rows),
        "statistics_window": len(tail),
        "cl_mean": mean(cls),
        "cl_std": sample_std(cls),
        "cl_peak_to_peak": max(cls) - min(cls),
        "cd_mean": mean(cds),
        "cd_std": sample_std(cds),
        "cd_peak_to_peak": max(cds) - min(cds),
        "final_density_residual_log10": residuals[-1] if residuals else None,
    }


def read_status(run_root: Path, case_name: str) -> dict[str, object]:
    rc_path = run_root / "status" / f"{case_name}.rc"
    rc: int | None = None
    if rc_path.is_file():
        try:
            rc = int(rc_path.read_text(encoding="utf-8").strip())
        except ValueError:
            rc = None
    manifests = sorted((run_root / "cases" / case_name / "logs").glob("*_run_manifest.json"))
    wrapper_result = None
    if manifests:
        try:
            wrapper_result = json.loads(manifests[-1].read_text(encoding="utf-8")).get("result")
        except (OSError, json.JSONDecodeError):
            wrapper_result = "UNREADABLE"
    accepted = rc == 0 and wrapper_result not in {"FAIL", "FAILED"}
    if accepted:
        label = "NUMERICAL_GATE_PASS"
    elif rc is not None:
        label = "FIELD_RETAINED_NUMERICAL_GATE_FAILED"
    else:
        label = "FIELD_RETAINED_ACCEPTANCE_UNKNOWN"
    return {
        "wrapper_return_code": rc,
        "wrapper_result": wrapper_result,
        "accepted": accepted,
        "status": label,
    }


def copy_if_present(source: Path, destination: Path) -> bool:
    if not source.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def copy_case_evidence(case_dir: Path, out_case: Path) -> list[str]:
    copied: list[str] = []
    for name in ("case_metrics.json", "shock_ridge_upper.csv", "shock_ridge_lower.csv"):
        if copy_if_present(case_dir / name, out_case / name):
            copied.append(name)
    manifests = sorted((case_dir / "logs").glob("*_run_manifest.json"))
    if manifests and copy_if_present(manifests[-1], out_case / "run_manifest.json"):
        copied.append("run_manifest.json")
    return copied


def read_field(path: Path) -> dict[str, object]:
    import numpy as np

    with path.open(newline="", encoding="utf-8-sig", errors="replace") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        columns = {
            "x": find_column(fields, ("x", "Points_0", "CoordinateX")),
            "y": find_column(fields, ("y", "Points_1", "CoordinateY")),
            "rho": find_column(fields, ("Density", "Rho")),
            "mx": find_column(fields, ("Momentum_x", "MomentumX", "RhoU")),
            "my": find_column(fields, ("Momentum_y", "MomentumY", "RhoV")),
            "energy": find_column(fields, ("Energy", "RhoE")),
        }
        missing = [name for name, column in columns.items() if column is None]
        if missing:
            raise ValueError(f"{path} is missing field columns: {', '.join(missing)}")
        data = {name: [] for name in columns}
        for row in reader:
            values = {name: finite_float(row.get(column)) for name, column in columns.items()}
            if all(value is not None for value in values.values()):
                for name, value in values.items():
                    data[name].append(float(value))  # type: ignore[arg-type]
    arrays = {name: np.asarray(values, dtype=float) for name, values in data.items()}
    rho = arrays["rho"]
    pressure = (GAMMA - 1.0) * (
        arrays["energy"] - 0.5 * (arrays["mx"] ** 2 + arrays["my"] ** 2) / rho
    )
    velocity = np.sqrt(arrays["mx"] ** 2 + arrays["my"] ** 2) / rho
    sound = np.sqrt(np.maximum(GAMMA * pressure / rho, 0.0))
    mach = np.divide(velocity, sound, out=np.full_like(velocity, np.nan), where=sound > 0)
    valid = (
        np.isfinite(arrays["x"])
        & np.isfinite(arrays["y"])
        & np.isfinite(rho)
        & np.isfinite(pressure)
        & np.isfinite(mach)
        & (rho > 0)
        & (pressure > 0)
    )
    return {
        "x": arrays["x"][valid],
        "y": arrays["y"][valid],
        "density": rho[valid],
        "pressure": pressure[valid],
        "mach": mach[valid],
        "total_rows": int(rho.size),
        "valid_rows": int(valid.sum()),
    }


def read_surface(path: Path) -> tuple[list[float], list[float]]:
    if not path.is_file():
        return [], []
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        xcol = find_column(fields, ("x", "Points_0", "CoordinateX"))
        ycol = find_column(fields, ("y", "Points_1", "CoordinateY"))
        if xcol is None or ycol is None:
            return [], []
        points = []
        for row in reader:
            x, y = finite_float(row.get(xcol)), finite_float(row.get(ycol))
            if x is not None and y is not None:
                points.append((x, y))
    return [p[0] for p in points], [p[1] for p in points]


def plot_fields(run_root: Path, summaries: list[dict[str, object]], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.tri as mtri
    import numpy as np

    fields: list[dict[str, object] | None] = []
    for angle in ANGLES:
        restart = run_root / "cases" / f"euler_alpha{angle}" / "restart_second_order.csv"
        fields.append(read_field(restart) if restart.is_file() else None)
    available = [field for field in fields if field is not None]
    if not available:
        raise FileNotFoundError("no final restart field is available for plotting")
    density_values = np.concatenate([f["density"] for f in available])
    mach_values = np.concatenate([f["mach"] for f in available])
    density_limits = tuple(np.nanpercentile(density_values, (1.0, 99.0)))
    mach_limits = (0.0, float(np.nanpercentile(mach_values, 99.5)))
    fig, axes = plt.subplots(2, 3, figsize=(15.2, 7.6), constrained_layout=True)
    for column, (angle, field, summary) in enumerate(zip(ANGLES, fields, summaries)):
        for row, (key, label, limits, cmap) in enumerate(
            (
                ("density", r"Density, $\rho$", density_limits, "viridis"),
                ("mach", "Mach number", mach_limits, "magma"),
            )
        ):
            ax = axes[row, column]
            if field is None:
                ax.text(0.5, 0.5, "final field missing", ha="center", va="center")
                ax.set_axis_off()
                continue
            x, y = field["x"], field["y"]
            view = (x >= -0.15) & (x <= 1.55) & (y >= -0.75) & (y <= 0.75)
            selected = np.flatnonzero(view)
            coordinates = np.column_stack((x[selected], y[selected]))
            _, unique_at = np.unique(np.round(coordinates, decimals=12), axis=0, return_index=True)
            selected = selected[np.sort(unique_at)]
            if selected.size < 3:
                ax.text(0.5, 0.5, "insufficient in-view points", ha="center", va="center")
                ax.set_axis_off()
                continue
            triangulation = mtri.Triangulation(x[selected], y[selected])
            triangles = triangulation.triangles
            xx, yy = x[selected], y[selected]
            edges = np.maximum.reduce(
                (
                    np.hypot(xx[triangles[:, 0]] - xx[triangles[:, 1]], yy[triangles[:, 0]] - yy[triangles[:, 1]]),
                    np.hypot(xx[triangles[:, 1]] - xx[triangles[:, 2]], yy[triangles[:, 1]] - yy[triangles[:, 2]]),
                    np.hypot(xx[triangles[:, 2]] - xx[triangles[:, 0]], yy[triangles[:, 2]] - yy[triangles[:, 0]]),
                )
            )
            positive = edges[edges > 0]
            if positive.size:
                triangulation.set_mask(edges > max(0.04, 12.0 * float(np.median(positive))))
            levels = np.linspace(limits[0], limits[1], 96)
            image = ax.tricontourf(
                triangulation,
                np.clip(field[key][selected], limits[0], limits[1]),
                levels=levels,
                cmap=cmap,
                extend="both",
            )
            sx, sy = read_surface(
                run_root / "cases" / f"euler_alpha{angle}" / "surface_flow_second_order.csv"
            )
            if sx:
                wall = [(xx, yy) for xx, yy in zip(sx, sy) if -0.05 <= xx <= 1.05 and abs(yy) <= 0.25]
                if wall:
                    ax.scatter(
                        [point[0] for point in wall],
                        [point[1] for point in wall],
                        color="black",
                        s=1.2,
                        zorder=5,
                    )
            ax.set_xlim(-0.1, 1.45)
            ax.set_ylim(-0.6, 0.6)
            ax.set_aspect("equal", adjustable="box")
            ax.set_xlabel(r"$x/c$")
            if column == 0:
                ax.set_ylabel(r"$y/c$")
            status = "PASS" if summary["accepted"] else "NOT ACCEPTED"
            ax.set_title(rf"$\alpha={angle}^\circ$ — {label}\n{status}", fontsize=11)
            fig.colorbar(image, ax=ax, shrink=0.82, pad=0.02)
    fig.suptitle(
        "Mach-3 sharp diamond airfoil: common-grid final fields\n"
        "Status reports the fail-closed numerical gate; it is not a grid-independence claim",
        fontsize=15,
    )
    fig.savefig(output, dpi=190, bbox_inches="tight")
    plt.close(fig)


def plot_histories(campaign_dir: Path, summaries: list[dict[str, object]], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(15.2, 4.4), constrained_layout=True)
    for angle, summary in zip(ANGLES, summaries):
        history = campaign_dir / "cases" / f"euler_alpha{angle}" / "aerodynamic_history.csv"
        with history.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        iteration = [int(row["iteration"]) for row in rows]
        cl = [float(row["cl"]) for row in rows]
        cd = [float(row["cd"]) for row in rows]
        residual = [finite_float(row["density_residual_log10"]) for row in rows]
        style = "-" if summary["accepted"] else "--"
        axes[0].plot(iteration, cl, style, label=rf"$\alpha={angle}^\circ$")
        axes[1].plot(iteration, cd, style, label=rf"$\alpha={angle}^\circ$")
        rx = [x for x, value in zip(iteration, residual) if value is not None]
        ry = [value for value in residual if value is not None]
        if ry:
            axes[2].plot(rx, ry, style, label=rf"$\alpha={angle}^\circ$")
    labels = ((r"$C_L$", "Lift history"), (r"$C_D$", "Drag history"), (r"$\log_{10} RMS(\rho)$", "Density residual"))
    for ax, (ylabel, title) in zip(axes, labels):
        ax.set_xlabel("Second-order iteration")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(alpha=0.25)
        ax.legend(frameon=False)
    fig.savefig(output, dpi=190, bbox_inches="tight")
    plt.close(fig)


def fmt(value: object, digits: int = 7) -> str:
    return "NA" if value is None else f"{float(value):.{digits}g}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_summary_csv(path: Path, summaries: list[dict[str, object]]) -> None:
    fields = (
        "case", "angle_deg", "status", "accepted", "wrapper_return_code",
        "history_rows", "statistics_window", "cl_mean", "cl_std", "cl_peak_to_peak",
        "cd_mean", "cd_std", "cd_peak_to_peak", "final_density_residual_log10",
    )
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summaries)


def write_readme(path: Path, run_name: str, summaries: list[dict[str, object]], overall: str) -> None:
    rows = []
    for item in summaries:
        rows.append(
            f"| {item['angle_deg']} | {item['status']} | {fmt(item.get('cl_mean'))} | "
            f"{fmt(item.get('cd_mean'))} | {fmt(item.get('final_density_residual_log10'))} |"
        )
    text = f"""# SU2 Mach-3 angle-of-attack results

Source campaign: `{run_name}`  
Overall audit label: **{overall}**

| AoA (deg) | fail-closed status | mean CL (last window) | mean CD (last window) | final log10 RMS(rho) |
|---:|---|---:|---:|---:|
{chr(10).join(rows)}

`NUMERICAL_GATE_PASS` means only that the configured numerical checks passed.
It does **not** establish grid independence. `FIELD_RETAINED_NUMERICAL_GATE_FAILED`
means that a native field exists and is useful for diagnosis/ML, but its forces
and shock metrics must not be presented as an accepted validation result.

## Compact evidence

- `fields_comparison.png`: density and Mach snapshots on the common grid;
- `aerodynamic_histories.png`: CL, CD, and density-residual histories;
- `aerodynamic_summary.csv` and `summary.json`: machine-readable statistics;
- `cases/*/aerodynamic_history.csv`: standardized histories;
- `cases/*/case_metrics.json` and `shock_ridge_*.csv`: copied when produced;
- `provenance/`: solver/run identity and capture markers.

The large restart CSV and VTU fields remain in the checksummed Unity archive;
they are intentionally not committed to ordinary Git history.
"""
    path.write_text(text, encoding="utf-8")


def export(run_root: Path, output_dir: Path, skip_plots: bool = False) -> dict[str, object]:
    run_root = run_root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []
    for angle in ANGLES:
        case_name = f"euler_alpha{angle}"
        case_dir = run_root / "cases" / case_name
        out_case = output_dir / "cases" / case_name
        history = case_dir / "history_second_order.csv"
        if not history.is_file():
            raise FileNotFoundError(f"required history missing: {history}")
        item: dict[str, object] = {"case": case_name, "angle_deg": angle}
        item.update(read_status(run_root, case_name))
        item.update(summarize_history(history, out_case / "aerodynamic_history.csv"))
        item["copied_evidence"] = copy_case_evidence(case_dir, out_case)
        summaries.append(item)

    root_failed = (run_root / "RUN_FAILED.txt").is_file()
    overall = "NUMERICAL_GATE_PASS" if all(item["accepted"] for item in summaries) and not root_failed else "MIXED_OR_FAILED_GATE_FIELDS_RETAINED"
    provenance = output_dir / "provenance"
    for name in (
        "RUN_STARTED.txt", "RUN_COMPLETE.txt", "RUN_FAILED.txt", "CAPTURE_COMPLETE.txt",
        "DATASET_MANIFEST.json", "DATASET_FAILED.json", "SHA256SUMS.txt",
    ):
        copy_if_present(run_root / name, provenance / name)
    for name in ("repo_commit.txt", "repo_status.txt", "SU2_CFD.sha256.txt", "python_version.txt", "system.txt"):
        copy_if_present(run_root / "provenance" / name, provenance / name)

    write_summary_csv(output_dir / "aerodynamic_summary.csv", summaries)
    report = {
        "dataset": "SU2 Euler Mach 3 sharp diamond airfoil AoA 0/4/8",
        "source_campaign": run_root.name,
        "overall_status": overall,
        "grid_independence_claim": False,
        "large_native_fields_committed_to_git": False,
        "statistics_window_max": WINDOW,
        "cases": summaries,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_readme(output_dir / "README.md", run_root.name, summaries, overall)
    if not skip_plots:
        plot_fields(run_root, summaries, output_dir / "fields_comparison.png")
        plot_histories(output_dir, summaries, output_dir / "aerodynamic_histories.png")

    files = sorted(path for path in output_dir.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.relative_to(output_dir).as_posix()}\n" for path in files),
        encoding="ascii",
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skip-plots", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = export(args.run_root, args.output_dir, args.skip_plots)
    print(json.dumps({"status": report["overall_status"], "output": str(args.output_dir.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
