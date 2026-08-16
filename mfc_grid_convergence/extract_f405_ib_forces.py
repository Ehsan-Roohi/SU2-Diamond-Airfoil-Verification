#!/usr/bin/env python3
"""Recover f405 immersed-boundary loads from existing MFC restart records."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import struct
from pathlib import Path


ALPHA_DEG = 40.0
RHO_INF = 1.0
U_INF = 3.0
CHORD = 1.0
Q_INF = 0.5 * RHO_INF * U_INF**2
RECORD_WIDTH = 20
DOUBLE_BYTES = 8


def step_from_name(path: Path) -> int:
    match = re.fullmatch(r"ib_state_(\d+)\.dat", path.name)
    if match is None:
        raise ValueError(f"Unexpected IB-state filename: {path.name}")
    return int(match.group(1))


def read_records(path: Path) -> list[tuple[float, ...]]:
    payload = path.read_bytes()
    record_bytes = RECORD_WIDTH * DOUBLE_BYTES
    if not payload or len(payload) % record_bytes:
        raise RuntimeError(
            f"Invalid MFC IB-state record: {path} ({len(payload)} bytes)"
        )
    return list(struct.iter_unpack(f"={RECORD_WIDTH}d", payload))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path, help="Completed MFC f405 case directory")
    parser.add_argument("--output-dir", type=Path, default=Path("f405_force_recovery"))
    args = parser.parse_args()

    case_dir = args.case_dir.expanduser().resolve()
    restart_dir = case_dir / "restart_data"
    files = sorted(restart_dir.glob("ib_state_*.dat"), key=step_from_name)
    if not files:
        raise SystemExit(f"No ib_state_*.dat files found in {restart_dir}")

    alpha = math.radians(ALPHA_DEG)
    rows: list[dict[str, float | int]] = []
    for path in files:
        records = read_records(path)
        if len(records) != 1:
            raise RuntimeError(
                f"Expected one immersed body in {path}, found {len(records)}"
            )
        time, force_x, force_y = records[0][:3]
        drag = force_x * math.cos(alpha) + force_y * math.sin(alpha)
        lift = -force_x * math.sin(alpha) + force_y * math.cos(alpha)
        rows.append(
            {
                "step": step_from_name(path),
                "time": time,
                "force_x": force_x,
                "force_y": force_y,
                "drag": drag,
                "lift": lift,
                "CD": drag / (Q_INF * CHORD),
                "CL": lift / (Q_INF * CHORD),
            }
        )

    if not all(
        math.isfinite(float(row[key]))
        for row in rows
        for key in ("time", "force_x", "force_y", "CD", "CL")
    ):
        raise RuntimeError("Recovered load history contains NaN or Inf")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "MFC_A40_F405_FORCE_HISTORY.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    active = [row for row in rows if float(row["time"]) > 0.0]
    late = [row for row in rows if float(row["time"]) >= 8.64]
    if not active or not late:
        raise RuntimeError("Force history does not contain the required time windows")

    summary = {
        "source_case": str(case_dir),
        "records": len(rows),
        "normalization": {
            "alpha_deg": ALPHA_DEG,
            "rho_inf": RHO_INF,
            "U_inf": U_INF,
            "q_inf": Q_INF,
            "chord": CHORD,
            "drag_axis": "+freestream",
            "lift_axis": "counterclockwise normal to freestream",
        },
        "noninitial": {
            "CD_mean": statistics.fmean(float(row["CD"]) for row in active),
            "CL_mean": statistics.fmean(float(row["CL"]) for row in active),
        },
        "late_time_t_ge_8p64": {
            "samples": len(late),
            "CD_mean": statistics.fmean(float(row["CD"]) for row in late),
            "CD_temporal_std": statistics.pstdev(float(row["CD"]) for row in late),
            "CL_mean": statistics.fmean(float(row["CL"]) for row in late),
            "CL_temporal_std": statistics.pstdev(float(row["CL"]) for row in late),
        },
    }
    json_path = args.output_dir / "MFC_A40_F405_FORCE_SUMMARY.json"
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"csv={csv_path.resolve()}")
    print(f"json={json_path.resolve()}")


if __name__ == "__main__":
    main()
