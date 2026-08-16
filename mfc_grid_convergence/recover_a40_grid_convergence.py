#!/usr/bin/env python3
"""Recover and compare MFC alpha=40 loads on the f180/f270/f405 grids."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import struct
import zipfile
from pathlib import Path


ALPHA_DEG = 40.0
RHO_INF = 1.0
U_INF = 3.0
CHORD = 1.0
Q_INF = 0.5 * RHO_INF * U_INF**2
LATE_START = 8.64
REFINEMENT_RATIO = 1.5
RECORD_WIDTH = 20
DOUBLE_BYTES = 8

LEVELS = {
    "f180": {
        "cells_per_chord": 180,
        "grid": "1980x1800",
        "dt": 1.0 / 3600.0,
        "save_every": 1944,
        "final_step": 48600,
    },
    "f270": {
        "cells_per_chord": 270,
        "grid": "2970x2700",
        "dt": 1.0 / 5400.0,
        "save_every": 2916,
        "final_step": 72900,
    },
    "f405": {
        "cells_per_chord": 405,
        "grid": "4455x4050",
        "dt": 1.0 / 8100.0,
        "save_every": 4374,
        "final_step": 109350,
    },
}


def read_records(path: Path) -> list[tuple[float, ...]]:
    payload = path.read_bytes()
    record_bytes = RECORD_WIDTH * DOUBLE_BYTES
    if not payload or len(payload) % record_bytes:
        raise RuntimeError(
            f"Invalid MFC IB-state record: {path} ({len(payload)} bytes)"
        )
    return list(struct.iter_unpack(f"={RECORD_WIDTH}d", payload))


def expected_steps(level: dict[str, object]) -> list[int]:
    save_every = int(level["save_every"])
    final_step = int(level["final_step"])
    return list(range(0, final_step + 1, save_every))


def discover_case(root: Path, label: str, level: dict[str, object]) -> Path:
    runs = root / "mfc_runs"
    final_name = f"ib_state_{level['final_step']}.dat"
    patterns = (
        f"*/restart_data/{final_name}",
        f"*/*/restart_data/{final_name}",
        f"*/*/*/restart_data/{final_name}",
    )
    candidates: list[tuple[float, Path]] = []
    required = expected_steps(level)
    for pattern in patterns:
        for final_path in runs.glob(pattern):
            case_dir = final_path.parent.parent.resolve()
            restart_dir = case_dir / "restart_data"
            if all((restart_dir / f"ib_state_{step}.dat").is_file() for step in required):
                candidates.append((final_path.stat().st_mtime, case_dir))
    if not candidates:
        raise RuntimeError(
            f"No complete {label} case with all {len(required)} expected IB-state "
            f"records was found below {runs}; expected final file {final_name}"
        )
    return max(candidates, key=lambda item: item[0])[1]


def extract_history(
    case_dir: Path, label: str, level: dict[str, object]
) -> list[dict[str, float | int]]:
    alpha = math.radians(ALPHA_DEG)
    rows: list[dict[str, float | int]] = []
    for step in expected_steps(level):
        path = case_dir / "restart_data" / f"ib_state_{step}.dat"
        records = read_records(path)
        if len(records) != 1:
            raise RuntimeError(
                f"Expected one immersed body in {path}, found {len(records)}"
            )
        time, force_x, force_y = records[0][:3]
        expected_time = step * float(level["dt"])
        if not math.isclose(time, expected_time, rel_tol=0.0, abs_tol=5.0e-8):
            raise RuntimeError(
                f"Unexpected physical time in {path}: {time} != {expected_time}"
            )
        drag = force_x * math.cos(alpha) + force_y * math.sin(alpha)
        lift = -force_x * math.sin(alpha) + force_y * math.cos(alpha)
        row: dict[str, float | int] = {
            "step": step,
            "time": time,
            "force_x": force_x,
            "force_y": force_y,
            "drag": drag,
            "lift": lift,
            "CD": drag / (Q_INF * CHORD),
            "CL": lift / (Q_INF * CHORD),
        }
        if not all(math.isfinite(float(value)) for value in row.values()):
            raise RuntimeError(f"{label} contains NaN or Inf at step {step}")
        rows.append(row)
    return rows


def summarize(
    case_dir: Path,
    label: str,
    level: dict[str, object],
    rows: list[dict[str, float | int]],
) -> dict[str, object]:
    active = [row for row in rows if float(row["time"]) > 0.0]
    late = [row for row in rows if float(row["time"]) >= LATE_START - 1.0e-9]
    if not active or len(late) < 2:
        raise RuntimeError(f"{label} does not contain the required time windows")

    def stats(key: str, selected: list[dict[str, float | int]]) -> dict[str, float]:
        values = [float(row[key]) for row in selected]
        mean = statistics.fmean(values)
        return {
            "mean": mean,
            "temporal_std": statistics.pstdev(values),
            "peak_to_peak": max(values) - min(values),
        }

    return {
        "level": label,
        "cells_per_chord": level["cells_per_chord"],
        "grid": level["grid"],
        "dt": level["dt"],
        "save_every": level["save_every"],
        "source_case": str(case_dir),
        "records": len(rows),
        "late_start": LATE_START,
        "late_samples": len(late),
        "noninitial": {
            "CD_mean": statistics.fmean(float(row["CD"]) for row in active),
            "CL_mean": statistics.fmean(float(row["CL"]) for row in active),
        },
        "late": {"CD": stats("CD", late), "CL": stats("CL", late)},
    }


def convergence_metric(
    metric: str, summaries: dict[str, dict[str, object]]
) -> dict[str, float | str | None]:
    coarse = float(summaries["f180"]["late"][metric]["mean"])  # type: ignore[index]
    medium = float(summaries["f270"]["late"][metric]["mean"])  # type: ignore[index]
    fine = float(summaries["f405"]["late"][metric]["mean"])  # type: ignore[index]
    medium_fine_change = abs(fine - medium) / max(abs(fine), 1.0e-30) * 100.0
    epsilon_21 = medium - fine
    epsilon_32 = coarse - medium
    result: dict[str, float | str | None] = {
        "f180_mean": coarse,
        "f270_mean": medium,
        "f405_mean": fine,
        "f270_to_f405_change_pct": medium_fine_change,
        "behavior": "undetermined",
        "observed_order": None,
        "richardson_extrapolated": None,
        "gci_f405_pct": None,
    }
    tolerance = 1.0e-14 * max(abs(coarse), abs(medium), abs(fine), 1.0)
    if abs(epsilon_21) <= tolerance and abs(epsilon_32) <= tolerance:
        result.update(
            behavior="indistinguishable",
            observed_order=None,
            richardson_extrapolated=fine,
            gci_f405_pct=0.0,
        )
        return result
    if epsilon_21 * epsilon_32 <= 0.0 or abs(epsilon_21) <= tolerance:
        result["behavior"] = "oscillatory_or_nonmonotonic"
        return result
    order = math.log(abs(epsilon_32 / epsilon_21)) / math.log(REFINEMENT_RATIO)
    if not math.isfinite(order) or order <= 0.0:
        result["behavior"] = "monotonic_but_not_convergent"
        result["observed_order"] = order
        return result
    denominator = REFINEMENT_RATIO**order - 1.0
    extrapolated = fine + (fine - medium) / denominator
    gci = (
        1.25
        * abs((fine - medium) / max(abs(fine), 1.0e-30))
        / denominator
        * 100.0
    )
    result.update(
        behavior="monotonic_convergence",
        observed_order=order,
        richardson_extrapolated=extrapolated,
        gci_f405_pct=gci,
    )
    return result


def write_csv(path: Path, rows: list[dict[str, float | int]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not (root / "mfc_runs").is_dir():
        raise SystemExit(f"MFC run directory was not found: {root / 'mfc_runs'}")
    output_dir.mkdir(parents=True, exist_ok=False)

    summaries: dict[str, dict[str, object]] = {}
    output_files: list[Path] = []
    for label, level in LEVELS.items():
        case_dir = discover_case(root, label, level)
        print(f"{label.upper()}_CASE={case_dir}", flush=True)
        rows = extract_history(case_dir, label, level)
        summary = summarize(case_dir, label, level, rows)
        summaries[label] = summary

        history_path = output_dir / f"MFC_A40_{label.upper()}_FORCE_HISTORY.csv"
        summary_path = output_dir / f"MFC_A40_{label.upper()}_FORCE_SUMMARY.json"
        write_csv(history_path, rows)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        output_files.extend((history_path, summary_path))

    metrics = {name: convergence_metric(name, summaries) for name in ("CD", "CL")}
    reasons: list[str] = []
    for name, metric in metrics.items():
        if metric["behavior"] not in ("monotonic_convergence", "indistinguishable"):
            reasons.append(f"{name}: {metric['behavior']}")
        elif float(metric["f270_to_f405_change_pct"]) > 1.0:
            reasons.append(f"{name}: f270-to-f405 change exceeds 1%")
        elif metric["gci_f405_pct"] is not None and float(metric["gci_f405_pct"]) > 1.0:
            reasons.append(f"{name}: f405 GCI exceeds 1%")
    recommendation = "RUN_F608" if reasons else "STOP_AT_F405"

    comparison = {
        "description": "MFC Mach-3 alpha=40 Euler/IBM three-grid load convergence",
        "normalization": {
            "alpha_deg": ALPHA_DEG,
            "rho_inf": RHO_INF,
            "U_inf": U_INF,
            "q_inf": Q_INF,
            "chord": CHORD,
        },
        "late_start": LATE_START,
        "refinement_ratio": REFINEMENT_RATIO,
        "levels": summaries,
        "metrics": metrics,
        "recommendation": recommendation,
        "recommendation_reasons": reasons,
    }
    comparison_json = output_dir / "MFC_A40_GRID_CONVERGENCE_SUMMARY.json"
    comparison_json.write_text(
        json.dumps(comparison, indent=2) + "\n", encoding="utf-8"
    )
    output_files.append(comparison_json)

    comparison_csv = output_dir / "MFC_A40_GRID_CONVERGENCE.csv"
    comparison_rows = []
    for name, metric in metrics.items():
        comparison_rows.append(
            {
                "metric": name,
                **metric,
                "f180_temporal_std": summaries["f180"]["late"][name]["temporal_std"],  # type: ignore[index]
                "f270_temporal_std": summaries["f270"]["late"][name]["temporal_std"],  # type: ignore[index]
                "f405_temporal_std": summaries["f405"]["late"][name]["temporal_std"],  # type: ignore[index]
            }
        )
    with comparison_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(comparison_rows[0]))
        writer.writeheader()
        writer.writerows(comparison_rows)
    output_files.append(comparison_csv)

    archive = output_dir / "MFC_A40_GRID_CONVERGENCE_RECOVERY.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for path in output_files:
            target.write(path, arcname=path.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = output_dir / f"{archive.name}.sha256.txt"
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

    print(json.dumps({"metrics": metrics, "recommendation": recommendation, "reasons": reasons}, indent=2))
    print(f"UPLOAD_THIS={archive}")
    print(f"UPLOAD_SHA256={checksum}")


if __name__ == "__main__":
    main()
