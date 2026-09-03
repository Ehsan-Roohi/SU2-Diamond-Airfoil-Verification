#!/usr/bin/env python3
"""Validate and package the complete Mach-3 Euler alpha=0/4/8 capture.

The package is deliberately lossless: native SU2 meshes, configurations,
restart CSVs, VTU volume fields, surface files, histories, logs, and provenance
are retained.  Numerical acceptance and byte-completeness are reported as
separate facts so a qualified/failed convergence gate never causes raw fields
to be deleted.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
import sys
import zipfile
from pathlib import Path
from typing import Iterable


ANGLES = (0, 4, 8)
CASE_TEMPLATE = "euler_alpha{}"
MESH_RELATIVE = Path("meshes/diamond_euler_sharp_medium_720x181.su2")
REQUIRED_CASE_FILES = (
    "startup.cfg",
    "second_order.cfg",
    "history_startup.csv",
    "history_second_order.csv",
    "restart_startup.csv",
    "restart_second_order.csv",
    "flow_startup.vtu",
    "flow_second_order.vtu",
    "surface_flow_startup.csv",
    "surface_flow_second_order.csv",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--job-id", default="local")
    parser.add_argument("--timestamp", help="UTC archive stamp; default is current time")
    return parser.parse_args()


def compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def find_column(fields: Iterable[str], candidates: tuple[str, ...]) -> str | None:
    lookup = {compact(field): field for field in fields}
    for candidate in candidates:
        if compact(candidate) in lookup:
            return lookup[compact(candidate)]
    return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mesh_point_count(path: Path) -> int:
    with path.open(encoding="ascii", errors="replace") as handle:
        for raw in handle:
            line = raw.split("%", 1)[0].strip()
            if line.upper().startswith("NPOIN"):
                return int(line.split("=", 1)[1].split()[0])
    raise ValueError(f"NPOIN was not found in {path}")


def inspect_restart(path: Path, expected_points: int) -> dict[str, object]:
    minimum_density = math.inf
    minimum_pressure = math.inf
    nonfinite_rows = 0
    nonpositive_rows = 0
    row_count = 0
    gamma = 1.4
    with path.open(newline="", encoding="utf-8-sig", errors="strict") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        columns = {
            "density": find_column(fields, ("Density", "Rho")),
            "momentum_x": find_column(fields, ("Momentum_x", "MomentumX", "RhoU")),
            "momentum_y": find_column(fields, ("Momentum_y", "MomentumY", "RhoV")),
            "energy": find_column(fields, ("Energy", "RhoE")),
            "x": find_column(fields, ("x", "Points_0", "CoordinateX")),
            "y": find_column(fields, ("y", "Points_1", "CoordinateY")),
        }
        missing = [name for name, value in columns.items() if value is None]
        if missing:
            raise ValueError(f"{path.name} is missing columns: {', '.join(missing)}")
        for row in reader:
            row_count += 1
            try:
                rho = float(row[columns["density"]])  # type: ignore[index]
                mx = float(row[columns["momentum_x"]])  # type: ignore[index]
                my = float(row[columns["momentum_y"]])  # type: ignore[index]
                energy = float(row[columns["energy"]])  # type: ignore[index]
                x = float(row[columns["x"]])  # type: ignore[index]
                y = float(row[columns["y"]])  # type: ignore[index]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"unreadable row {row_count} in {path.name}") from exc
            if not all(math.isfinite(value) for value in (rho, mx, my, energy, x, y)):
                nonfinite_rows += 1
                continue
            if rho <= 0.0:
                nonpositive_rows += 1
                continue
            u = mx / rho
            v = my / rho
            pressure = (gamma - 1.0) * (energy - 0.5 * rho * (u * u + v * v))
            minimum_density = min(minimum_density, rho)
            minimum_pressure = min(minimum_pressure, pressure)
            if pressure <= 0.0:
                nonpositive_rows += 1
    if row_count != expected_points:
        raise ValueError(
            f"{path.name} has {row_count} rows; matching mesh has {expected_points} points"
        )
    if nonfinite_rows or nonpositive_rows:
        raise ValueError(
            f"{path.name} has nonfinite={nonfinite_rows}, nonpositive={nonpositive_rows} rows"
        )
    return {
        "rows": row_count,
        "columns": fields,
        "minimum_density": minimum_density,
        "minimum_pressure": minimum_pressure,
        "nonfinite_rows": nonfinite_rows,
        "nonpositive_rows": nonpositive_rows,
    }


def inspect_vtu(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        head = handle.read(4096)
    if b"VTKFile" not in head or b"UnstructuredGrid" not in head:
        raise ValueError(f"{path.name} is not a readable VTK UnstructuredGrid file")
    return {"size_bytes": path.stat().st_size, "vtk_header_present": True}


def inspect_history(path: Path) -> dict[str, object]:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{path.name} contains no history rows")
    fields = list(rows[0])
    residual_col = find_column(fields, ("RMS_DENSITY", "RMS[Rho]", "RMSRho"))
    cl_col = find_column(fields, ("LIFT", "CL"))
    cd_col = find_column(fields, ("DRAG", "CD"))

    def values(column: str | None) -> list[float]:
        if column is None:
            return []
        answer = []
        for row in rows:
            try:
                value = float(row[column])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(value):
                answer.append(value)
        return answer

    residuals, cls, cds = values(residual_col), values(cl_col), values(cd_col)
    window = min(200, len(rows))
    return {
        "rows": len(rows),
        "final_density_residual": residuals[-1] if residuals else None,
        "cl_last_window_mean": sum(cls[-window:]) / window if len(cls) >= window else None,
        "cl_last_window_peak_to_peak": (
            max(cls[-window:]) - min(cls[-window:]) if len(cls) >= window else None
        ),
        "cd_last_window_mean": sum(cds[-window:]) / window if len(cds) >= window else None,
        "cd_last_window_peak_to_peak": (
            max(cds[-window:]) - min(cds[-window:]) if len(cds) >= window else None
        ),
    }


def latest_wrapper_manifest(case_dir: Path) -> dict[str, object] | None:
    candidates = sorted((case_dir / "logs").glob("*_run_manifest.json"))
    if not candidates:
        return None
    return json.loads(candidates[-1].read_text(encoding="utf-8"))


def all_files(root: Path) -> list[Path]:
    ignored = {"DATASET_MANIFEST.json", "SHA256SUMS.txt", "CAPTURE_COMPLETE.txt"}
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.name not in ignored
    )


def main() -> int:
    args = parse_args()
    run_root = args.run_root.resolve()
    archive_dir = args.archive_dir.resolve()
    archive_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    cases: dict[str, object] = {}
    mesh = run_root / MESH_RELATIVE
    if not mesh.is_file() or mesh.stat().st_size == 0:
        failures.append(f"missing mesh: {MESH_RELATIVE}")
        point_count = 0
    else:
        try:
            point_count = mesh_point_count(mesh)
        except (OSError, ValueError) as exc:
            failures.append(str(exc))
            point_count = 0

    for angle in ANGLES:
        name = CASE_TEMPLATE.format(angle)
        case_dir = run_root / "cases" / name
        missing = [item for item in REQUIRED_CASE_FILES if not (case_dir / item).is_file()]
        empty = [
            item for item in REQUIRED_CASE_FILES
            if (case_dir / item).is_file() and (case_dir / item).stat().st_size == 0
        ]
        if missing:
            failures.append(f"{name}: missing {', '.join(missing)}")
        if empty:
            failures.append(f"{name}: empty {', '.join(empty)}")
        record: dict[str, object] = {"angle_deg": angle, "missing": missing, "empty": empty}
        if not missing and not empty and point_count:
            try:
                record["final_restart"] = inspect_restart(
                    case_dir / "restart_second_order.csv", point_count
                )
                record["final_volume"] = inspect_vtu(case_dir / "flow_second_order.vtu")
                record["final_history"] = inspect_history(
                    case_dir / "history_second_order.csv"
                )
            except (OSError, ValueError, csv.Error) as exc:
                failures.append(f"{name}: {exc}")
        try:
            wrapper = latest_wrapper_manifest(case_dir)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            wrapper = None
            failures.append(f"{name}: unreadable wrapper manifest: {exc}")
        record["wrapper_result"] = wrapper.get("result") if wrapper else "MISSING"
        status_path = run_root / "status" / f"{name}.rc"
        if status_path.is_file():
            try:
                record["wrapper_return_code"] = int(status_path.read_text().strip())
            except ValueError:
                record["wrapper_return_code"] = None
        cases[name] = record

    if failures:
        failure_record = {
            "capture_complete": False,
            "checked_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "failures": failures,
            "cases": cases,
        }
        (run_root / "DATASET_FAILED.json").write_text(
            json.dumps(failure_record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print("FAILED_CAPTURE: " + " | ".join(failures), file=sys.stderr)
        return 2

    source_files = all_files(run_root)
    inventory = []
    for path in source_files:
        inventory.append(
            {
                "path": path.relative_to(run_root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    wrapper_codes = [
        record.get("wrapper_return_code")
        for record in cases.values()
        if isinstance(record, dict)
    ]
    science_qa_passed = bool(wrapper_codes) and all(code == 0 for code in wrapper_codes)
    stamp = args.timestamp or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = {
        "dataset": "SU2 Euler Mach 3 diamond airfoil alpha=0,4,8 full native capture",
        "scope": (
            "New common-topology full-field capture for shock-detection/ML use; "
            "not the unavailable refined-triangular Appendix validation archive."
        ),
        "intended_use": "shock-field inspection, segmentation, and reproducible teaching runs",
        "capture_complete": True,
        "science_qa_passed": science_qa_passed,
        "science_qa_note": (
            "All fail-closed wrappers returned zero."
            if science_qa_passed
            else "Raw capture is complete; inspect per-case wrapper results before scientific use."
        ),
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "slurm_job_id": str(args.job_id),
        "solver_family": "SU2 v8.5.0 Euler, HLLC, second-order MUSCL/Venkatakrishnan",
        "mach": 3.0,
        "angles_deg": list(ANGLES),
        "mesh": MESH_RELATIVE.as_posix(),
        "mesh_family": "common sharp Euler O-grid 720x181",
        "mesh_points": point_count,
        "cases": cases,
        "files": inventory,
    }
    manifest_path = run_root / "DATASET_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    checksums = [f"{item['sha256']}  {item['path']}" for item in inventory]
    checksums.append(f"{sha256(manifest_path)}  DATASET_MANIFEST.json")
    (run_root / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    (run_root / "CAPTURE_COMPLETE.txt").write_text(
        "Complete native SU2 capture for alpha=0,4,8.\n"
        f"science_qa_passed={str(science_qa_passed).lower()}\n",
        encoding="utf-8",
    )

    archive_name = f"SU2_EULER_M3_AOA_0_4_8_FULL_{stamp}_JOB{args.job_id}.zip"
    archive = archive_dir / archive_name
    prefix = "SU2_EULER_M3_AOA_0_4_8_FULL"
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True
    ) as output:
        for path in sorted(path for path in run_root.rglob("*") if path.is_file()):
            output.write(path, f"{prefix}/{path.relative_to(run_root).as_posix()}")
    with zipfile.ZipFile(archive) as check:
        bad = check.testzip()
        if bad is not None:
            raise RuntimeError(f"ZIP CRC verification failed at {bad}")
    digest = sha256(archive)
    checksum_path = archive.with_suffix(archive.suffix + ".sha256.txt")
    checksum_path.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"A048_ARCHIVE={archive}")
    print(f"A048_ARCHIVE_SHA256={digest}")
    print(f"A048_SCIENCE_QA_PASSED={str(science_qa_passed).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
