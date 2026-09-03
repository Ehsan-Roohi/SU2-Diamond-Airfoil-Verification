#!/usr/bin/env python3
"""Extract native MFC immersed-boundary loads for the alpha=40 Reynolds suite.

The workflow reads MFC ``restart_data/ib_state_<step>.dat`` records directly.
For the pinned MFC source (0c9a1d43), every immersed body is represented by
20 native-endian real(wp) values.  Values 1:4 are time and Cartesian force.
No force is reconstructed from the downsampled computer-vision tensors.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import statistics
import struct
import tempfile
import zipfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np


ALPHA_DEG = 40.0
RHO_INF = 1.0
U_INF = 3.0
CHORD = 1.0
Q_INF = 0.5 * RHO_INF * U_INF**2
RECORD_WIDTH = 20
DOUBLE_BYTES = 8
ZERO_FORCE_TOL = 1.0e-12
BOUNDARY_ATOL = 1.0e-10
BOUNDARY_RTOL = 1.0e-8
MFC_SOURCE_REV = "0c9a1d434410175ac483b8d71646455444e3b7eb"
MFC_SCHEMA_URL = (
    "https://github.com/MFlowCode/MFC/blob/"
    f"{MFC_SOURCE_REV}/src/simulation/m_data_output.fpp#L929-L1067"
)

CASE_ORDER = (
    "re1e4_f180",
    "re1e4_f270",
    "re5e4_f180",
    "re1e5_f180",
    "re1e6_f270",
)

CASE_LABELS = {
    "re1e4_f180": r"$Re_c=10^4$, f180",
    "re1e4_f270": r"$Re_c=10^4$, f270",
    "re5e4_f180": r"$Re_c=5\times10^4$, f180",
    "re1e5_f180": r"$Re_c=10^5$, f180",
    "re1e6_f270": r"$Re_c=10^6$, f270",
}

CASE_METADATA = {
    "re1e4_f180": (1.0e4, "f180"),
    "re1e4_f270": (1.0e4, "f270"),
    "re5e4_f180": (5.0e4, "f180"),
    "re1e5_f180": (1.0e5, "f180"),
    "re1e6_f270": (1.0e6, "f270"),
}

COLORS = {
    "re1e4_f180": "#277da1",
    "re1e4_f270": "#577590",
    "re5e4_f180": "#43aa8b",
    "re1e5_f180": "#f9c74f",
    "re1e6_f270": "#f94144",
}

HISTORY_FIELDS = (
    "case",
    "Re_c",
    "grid",
    "role",
    "segment_id",
    "stage",
    "source_status",
    "source_file",
    "step",
    "time",
    "force_x",
    "force_y",
    "force_z",
    "drag",
    "lift",
    "CD",
    "CL",
)

SUMMARY_FIELDS = (
    "case",
    "Re_c",
    "grid",
    "window",
    "t_start",
    "t_stop",
    "status",
    "segment_id",
    "samples",
    "coverage_fraction",
    "force_x_mean",
    "force_y_mean",
    "drag_mean",
    "lift_mean",
    "CL_mean",
    "CL_temporal_std",
    "CL_peak_to_peak",
    "CL_slope_per_time",
    "CL_window_drift_percent",
    "CD_mean",
    "CD_temporal_std",
    "CD_peak_to_peak",
    "CD_slope_per_time",
    "CD_window_drift_percent",
    "L_over_D_from_means",
)


@dataclass(frozen=True)
class SourceSpec:
    case: str
    reynolds: float
    grid: str
    role: str
    stage: str
    case_dir: Path
    dt: float
    start_step: int
    stop_step: int
    save_stride: int
    order: int


@dataclass
class SourceInventory:
    spec: SourceSpec
    status: str
    usable: bool
    records: list[dict[str, Any]]
    expected_count: int
    missing_steps: list[int]
    parse_errors: list[str]
    time_mismatches: list[str]
    per_process_files: int
    maximum_noninitial_force: float | None


def step_from_name(path: Path) -> int:
    match = re.fullmatch(r"ib_state_(\d+)\.dat", path.name)
    if match is None:
        raise ValueError(f"unexpected global IB-state filename: {path.name}")
    return int(match.group(1))


def read_global_ib_records(path: Path) -> list[tuple[float, ...]]:
    """Read a global, stream-unformatted MFC IB state file."""
    payload = path.read_bytes()
    record_bytes = RECORD_WIDTH * DOUBLE_BYTES
    if not payload or len(payload) % record_bytes:
        raise RuntimeError(
            f"invalid MFC global IB-state file ({len(payload)} bytes): {path}"
        )
    return list(struct.iter_unpack(f"={RECORD_WIDTH}d", payload))


def force_coefficients(force_x: float, force_y: float) -> tuple[float, float, float, float]:
    alpha = math.radians(ALPHA_DEG)
    drag = force_x * math.cos(alpha) + force_y * math.sin(alpha)
    lift = -force_x * math.sin(alpha) + force_y * math.cos(alpha)
    return drag, lift, drag / (Q_INF * CHORD), lift / (Q_INF * CHORD)


def _time_tolerance(expected_time: float, dt: float) -> float:
    return max(5.0e-10, abs(expected_time) * 2.0e-8, abs(dt) * 0.05)


def scan_source(spec: SourceSpec) -> SourceInventory:
    restart_dir = spec.case_dir / "restart_data"
    expected_steps = list(range(spec.start_step, spec.stop_step + 1, spec.save_stride))
    expected_set = set(expected_steps)
    parse_errors: list[str] = []
    time_mismatches: list[str] = []
    records: list[dict[str, Any]] = []

    if not spec.case_dir.is_dir():
        return SourceInventory(
            spec, "MISSING_CASE_DIR", False, [], len(expected_steps), expected_steps,
            [], [], 0, None
        )
    if not restart_dir.is_dir():
        return SourceInventory(
            spec, "MISSING_RESTART_DIR", False, [], len(expected_steps), expected_steps,
            [], [], 0, None
        )

    files = sorted(restart_dir.glob("ib_state_*.dat"), key=step_from_name)
    per_process_files = sum(
        1 for _ in restart_dir.glob("lustre_*/ib_state_*_*.dat")
    )
    if not files:
        status = "PER_PROCESS_ONLY_UNSUPPORTED" if per_process_files else "MISSING_IB_STATE"
        return SourceInventory(
            spec, status, False, [], len(expected_steps), expected_steps,
            [], [], per_process_files, None
        )

    seen_steps: set[int] = set()
    for path in files:
        step = step_from_name(path)
        if step not in expected_set:
            continue
        try:
            body_records = read_global_ib_records(path)
            if len(body_records) != 1:
                raise RuntimeError(
                    f"expected one immersed body, found {len(body_records)}"
                )
            values = body_records[0]
            if not all(math.isfinite(value) for value in values):
                raise RuntimeError("record contains NaN or Inf")
            time_value, force_x, force_y, force_z = values[:4]
            expected_time = step * spec.dt
            if not math.isclose(
                time_value,
                expected_time,
                rel_tol=0.0,
                abs_tol=_time_tolerance(expected_time, spec.dt),
            ):
                time_mismatches.append(
                    f"{path}: time={time_value:.17g}, step*dt={expected_time:.17g}"
                )
            drag, lift, cd, cl = force_coefficients(force_x, force_y)
            records.append(
                {
                    "case": spec.case,
                    "Re_c": spec.reynolds,
                    "grid": spec.grid,
                    "role": spec.role,
                    "stage": spec.stage,
                    "source_file": str(path.resolve()),
                    "step": step,
                    "time": time_value,
                    "force_x": force_x,
                    "force_y": force_y,
                    "force_z": force_z,
                    "drag": drag,
                    "lift": lift,
                    "CD": cd,
                    "CL": cl,
                    "expected_stride": spec.save_stride,
                    "stage_order": spec.order,
                }
            )
            seen_steps.add(step)
        except Exception as exc:  # Preserve an auditable partial inventory.
            parse_errors.append(f"{path}: {exc}")

    records.sort(key=lambda row: int(row["step"]))
    missing_steps = sorted(expected_set - seen_steps)
    noninitial = [
        row for row in records if float(row["time"]) > spec.start_step * spec.dt + 1.0e-12
    ]
    force_magnitudes = [
        math.hypot(float(row["force_x"]), float(row["force_y"]))
        for row in noninitial
    ]
    max_force = max(force_magnitudes) if force_magnitudes else None

    if parse_errors:
        status, usable = "CORRUPT_IB_STATE", False
    elif time_mismatches:
        status, usable = "TIME_STEP_MISMATCH", False
    elif not records:
        status, usable = "NO_EXPECTED_IB_STATE", False
    elif max_force is not None and max_force <= ZERO_FORCE_TOL:
        status, usable = "ZERO_NATIVE_LOADS", False
    elif missing_steps:
        status, usable = "INCOMPLETE_BUT_USABLE", True
    else:
        status, usable = "VALID", True

    for row in records:
        row["source_status"] = status

    return SourceInventory(
        spec=spec,
        status=status,
        usable=usable,
        records=records,
        expected_count=len(expected_steps),
        missing_steps=missing_steps,
        parse_errors=parse_errors,
        time_mismatches=time_mismatches,
        per_process_files=per_process_files,
        maximum_noninitial_force=max_force,
    )


def records_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    keys = ("time", "force_x", "force_y", "force_z")
    return all(
        math.isclose(
            float(left[key]),
            float(right[key]),
            rel_tol=BOUNDARY_RTOL,
            abs_tol=BOUNDARY_ATOL,
        )
        for key in keys
    )


def boundary_audit(inventories: Sequence[SourceInventory]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_case: dict[str, list[SourceInventory]] = {}
    for inventory in inventories:
        by_case.setdefault(inventory.spec.case, []).append(inventory)

    for case, members in by_case.items():
        members.sort(key=lambda item: item.spec.order)
        for left, right in zip(members[:-1], members[1:]):
            if left.spec.stop_step != right.spec.start_step:
                continue
            boundary_step = left.spec.stop_step
            left_record = next(
                (row for row in left.records if int(row["step"]) == boundary_step), None
            )
            right_record = next(
                (row for row in right.records if int(row["step"]) == boundary_step), None
            )
            if left_record is None and right_record is None:
                status = "MISSING_BOTH"
            elif left_record is None:
                status = "MISSING_LEFT"
            elif right_record is None:
                status = "MISSING_RIGHT"
            elif records_equivalent(left_record, right_record):
                status = "PASS"
            else:
                status = "FAIL_MISMATCH"

            def delta(key: str) -> float | str:
                if left_record is None or right_record is None:
                    return ""
                return float(right_record[key]) - float(left_record[key])

            rows.append(
                {
                    "case": case,
                    "boundary_step": boundary_step,
                    "boundary_time": boundary_step * left.spec.dt,
                    "left_stage": left.spec.stage,
                    "right_stage": right.spec.stage,
                    "left_file": "" if left_record is None else left_record["source_file"],
                    "right_file": "" if right_record is None else right_record["source_file"],
                    "status": status,
                    "delta_time": delta("time"),
                    "delta_force_x": delta("force_x"),
                    "delta_force_y": delta("force_y"),
                    "delta_CD": delta("CD"),
                    "delta_CL": delta("CL"),
                }
            )
    return rows


def _source_sequences(inventory: SourceInventory) -> list[list[dict[str, Any]]]:
    if not inventory.usable or not inventory.records:
        return []
    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in inventory.records:
        if current:
            step_delta = int(row["step"]) - int(current[-1]["step"])
            time_delta = float(row["time"]) - float(current[-1]["time"])
            if step_delta != inventory.spec.save_stride or time_delta <= 0.0:
                result.append(current)
                current = []
        current.append(dict(row))
    if current:
        result.append(current)
    return result


def merge_histories(inventories: Sequence[SourceInventory]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_case: dict[str, list[SourceInventory]] = {}
    for inventory in inventories:
        by_case.setdefault(inventory.spec.case, []).append(inventory)

    for case_index, case in enumerate(CASE_ORDER):
        members = sorted(by_case.get(case, []), key=lambda item: item.spec.order)
        segment_number = 0
        active: list[dict[str, Any]] = []

        def flush() -> None:
            nonlocal active, segment_number
            if not active:
                return
            segment_number += 1
            segment_id = f"{case}_seg{segment_number:02d}"
            for item in active:
                item["segment_id"] = segment_id
                merged.append(item)
            active = []

        for inventory in members:
            sequences = _source_sequences(inventory)
            if not sequences:
                flush()
                continue
            for sequence in sequences:
                if not active:
                    active = sequence
                    continue
                if int(sequence[0]["step"]) == int(active[-1]["step"]):
                    if records_equivalent(active[-1], sequence[0]):
                        active.extend(sequence[1:])
                    else:
                        flush()
                        active = sequence
                else:
                    flush()
                    active = sequence
        flush()

    merged.sort(
        key=lambda row: (
            CASE_ORDER.index(str(row["case"])),
            str(row["segment_id"]),
            float(row["time"]),
            int(row["stage_order"]),
        )
    )
    return merged


def _window_stats(
    history: Sequence[dict[str, Any]],
    case: str,
    window: str,
    t_start: float,
    t_stop: float,
) -> dict[str, Any]:
    candidates: dict[str, list[dict[str, Any]]] = {}
    for row in history:
        if row["case"] != case:
            continue
        time_value = float(row["time"])
        if t_start - 1.0e-9 <= time_value <= t_stop + 1.0e-9:
            candidates.setdefault(str(row["segment_id"]), []).append(row)

    default_reynolds, default_grid = CASE_METADATA[case]
    base = {
        "case": case,
        "Re_c": next(
            (float(row["Re_c"]) for row in history if row["case"] == case),
            default_reynolds,
        ),
        "grid": next(
            (str(row["grid"]) for row in history if row["case"] == case),
            default_grid,
        ),
        "window": window,
        "t_start": t_start,
        "t_stop": t_stop,
    }
    if not candidates:
        return {
            **base,
            "status": "NO_USABLE_NATIVE_HISTORY",
            "segment_id": "",
            "samples": 0,
            "coverage_fraction": 0.0,
            **{key: "" for key in SUMMARY_FIELDS[9:]},
        }

    segment_id, rows = max(candidates.items(), key=lambda item: len(item[1]))
    rows.sort(key=lambda row: float(row["time"]))
    times = np.asarray([float(row["time"]) for row in rows], dtype=float)
    coverage = 0.0 if t_stop <= t_start else (times[-1] - times[0]) / (t_stop - t_start)
    status = "VALID" if len(rows) >= 5 and coverage >= 0.90 else "INSUFFICIENT_COVERAGE"

    result: dict[str, Any] = {
        **base,
        "status": status,
        "segment_id": segment_id,
        "samples": len(rows),
        "coverage_fraction": coverage,
    }
    for force_name in ("force_x", "force_y", "drag", "lift"):
        result[f"{force_name}_mean"] = float(
            np.mean([float(row[force_name]) for row in rows])
        )
    for coefficient in ("CL", "CD"):
        values = np.asarray([float(row[coefficient]) for row in rows], dtype=float)
        if len(values) >= 2 and np.ptp(times) > 0.0:
            slope = float(np.polyfit(times - times.mean(), values, 1)[0])
        else:
            slope = ""
        mean_value = float(np.mean(values))
        drift = (
            100.0 * slope * (t_stop - t_start) / abs(mean_value)
            if isinstance(slope, float) and math.isfinite(slope) and abs(mean_value) > 1.0e-14
            else ""
        )
        result[f"{coefficient}_mean"] = mean_value
        result[f"{coefficient}_temporal_std"] = float(np.std(values, ddof=0))
        result[f"{coefficient}_peak_to_peak"] = float(np.ptp(values))
        result[f"{coefficient}_slope_per_time"] = slope
        result[f"{coefficient}_window_drift_percent"] = drift
    result["L_over_D_from_means"] = (
        result["CL_mean"] / result["CD_mean"]
        if abs(result["CD_mean"]) > 1.0e-14
        else ""
    )
    return result


def summarize_windows(history: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        _window_stats(history, case, "early_t3_t6", 3.0, 6.0)
        for case in CASE_ORDER
    ]
    rows.append(
        _window_stats(history, "re1e6_f270", "mature_t26_t31", 26.0, 31.0)
    )
    return rows


def build_comparisons(summary: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = {
        (str(row["case"]), str(row["window"])): row
        for row in summary
        if row["status"] == "VALID"
    }
    comparisons: list[dict[str, Any]] = []

    def append_comparison(
        name: str,
        baseline_key: tuple[str, str],
        target_key: tuple[str, str],
        interpretation: str,
    ) -> None:
        baseline = valid.get(baseline_key)
        target = valid.get(target_key)
        row: dict[str, Any] = {
            "comparison": name,
            "baseline_case": baseline_key[0],
            "target_case": target_key[0],
            "window": baseline_key[1],
            "interpretation": interpretation,
        }
        if baseline is None or target is None:
            row.update(
                status="UNAVAILABLE",
                delta_CL_mean="",
                delta_CD_mean="",
                delta_CL_percent="",
                delta_CD_percent="",
            )
        else:
            delta_cl = float(target["CL_mean"]) - float(baseline["CL_mean"])
            delta_cd = float(target["CD_mean"]) - float(baseline["CD_mean"])
            row.update(
                status="VALID",
                delta_CL_mean=delta_cl,
                delta_CD_mean=delta_cd,
                delta_CL_percent=(
                    100.0 * delta_cl / abs(float(baseline["CL_mean"]))
                    if abs(float(baseline["CL_mean"])) > 1.0e-14 else ""
                ),
                delta_CD_percent=(
                    100.0 * delta_cd / abs(float(baseline["CD_mean"]))
                    if abs(float(baseline["CD_mean"])) > 1.0e-14 else ""
                ),
            )
        comparisons.append(row)

    append_comparison(
        "Re1e4_grid_f180_to_f270",
        ("re1e4_f180", "early_t3_t6"),
        ("re1e4_f270", "early_t3_t6"),
        "grid sensitivity at fixed Reynolds number",
    )
    append_comparison(
        "Re_f180_1e4_to_5e4",
        ("re1e4_f180", "early_t3_t6"),
        ("re5e4_f180", "early_t3_t6"),
        "same-grid Reynolds screening",
    )
    append_comparison(
        "Re_f180_1e4_to_1e5",
        ("re1e4_f180", "early_t3_t6"),
        ("re1e5_f180", "early_t3_t6"),
        "same-grid Reynolds screening",
    )
    return comparisons


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _format_value(value: Any, digits: int = 4) -> str:
    if value == "" or value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return "-" if not math.isfinite(number) else f"{number:.{digits}g}"


def _plot_history(
    axis: Any,
    history: Sequence[dict[str, Any]],
    case: str,
    coefficient: str,
    label: str | None = None,
) -> bool:
    segments: dict[str, list[dict[str, Any]]] = {}
    for row in history:
        if row["case"] == case:
            segments.setdefault(str(row["segment_id"]), []).append(row)
    plotted = False
    for index, rows in enumerate(segments.values()):
        rows.sort(key=lambda row: float(row["time"]))
        axis.plot(
            [float(row["time"]) for row in rows],
            [float(row[coefficient]) for row in rows],
            color=COLORS[case],
            linewidth=1.45,
            label=label if index == 0 else None,
        )
        plotted = True
    return plotted


def _apply_plot_style(axis: Any, ylabel: str) -> None:
    axis.set_ylabel(ylabel)
    axis.grid(True, alpha=0.22, linewidth=0.7)
    axis.spines[["top", "right"]].set_visible(False)


def render_email_figure(
    path: Path,
    history: Sequence[dict[str, Any]],
    summary: Sequence[dict[str, Any]],
    overall_status: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.size": 9.5,
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "legend.fontsize": 8.5,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )
    fig, axes = plt.subplots(2, 2, figsize=(12.8, 7.2), constrained_layout=True)
    same_grid = ("re1e4_f180", "re5e4_f180", "re1e5_f180")
    for coefficient, axis, title in (
        ("CL", axes[0, 0], "(a) Native lift histories - same f180 grid"),
        ("CD", axes[0, 1], "(b) Native drag histories - same f180 grid"),
    ):
        any_line = False
        for case in same_grid:
            any_line |= _plot_history(axis, history, case, coefficient, CASE_LABELS[case])
        axis.set_xlim(0.0, 6.0)
        axis.set_xlabel(r"convective time $tU_\infty/c$")
        _apply_plot_style(axis, rf"${coefficient}$")
        axis.set_title(title, loc="left", fontweight="bold")
        if any_line:
            axis.legend(frameon=False, ncol=1)
        else:
            axis.text(0.5, 0.5, "No usable native history", ha="center", va="center", transform=axis.transAxes)

    early = {
        row["case"]: row
        for row in summary
        if row["window"] == "early_t3_t6" and row["status"] == "VALID"
    }
    for coefficient, axis, title in (
        ("CL", axes[1, 0], r"(c) Mean $C_L$, $3\leq t\leq6$ - f180"),
        ("CD", axes[1, 1], r"(d) Mean $C_D$, $3\leq t\leq6$ - f180"),
    ):
        cases = [case for case in same_grid if case in early]
        if cases:
            x_values = [float(early[case]["Re_c"]) for case in cases]
            means = [float(early[case][f"{coefficient}_mean"]) for case in cases]
            errors = [float(early[case][f"{coefficient}_temporal_std"]) for case in cases]
            axis.errorbar(
                x_values,
                means,
                yerr=errors,
                color="#343a40",
                marker="o",
                markersize=6,
                linewidth=1.3,
                capsize=4,
            )
            for case, x_value, mean in zip(cases, x_values, means):
                axis.scatter(x_value, mean, s=48, color=COLORS[case], zorder=3)
            axis.set_xscale("log")
            axis.set_xticks([1.0e4, 5.0e4, 1.0e5])
            axis.set_xticklabels([r"$10^4$", r"$5\times10^4$", r"$10^5$"])
        else:
            axis.text(0.5, 0.5, "Insufficient valid cases", ha="center", va="center", transform=axis.transAxes)
        axis.set_xlabel(r"chord Reynolds number $Re_c$")
        _apply_plot_style(axis, rf"mean ${coefficient}$ (bars: temporal std.)")
        axis.set_title(title, loc="left", fontweight="bold")

    fig.suptitle(
        "Mach 3, alpha = 40 deg diamond airfoil: native MFC immersed-boundary loads\n"
        f"analysis status: {overall_status}",
        fontsize=14,
        fontweight="bold",
    )
    fig.savefig(path, dpi=240)
    plt.close(fig)


def render_pdf(
    path: Path,
    history: Sequence[dict[str, Any]],
    inventories: Sequence[SourceInventory],
    continuity: Sequence[dict[str, Any]],
    summary: Sequence[dict[str, Any]],
    comparisons: Sequence[dict[str, Any]],
    overall_status: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.titlesize": 11,
            "axes.labelsize": 9.5,
            "legend.fontsize": 8,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )
    with PdfPages(
        path,
        metadata={
            "Title": "Native MFC force evidence for Tim Colonius",
            "Author": "Reproducible Unity post-processing workflow",
            "Subject": "Mach 3 alpha=40 Reynolds and grid effects",
        },
    ) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.text(0.07, 0.95, "Native MFC force evidence", fontsize=20, fontweight="bold")
        fig.text(
            0.07,
            0.915,
            "Mach 3 diamond airfoil, alpha = 40 deg",
            fontsize=12,
            color="#495057",
        )
        status_color = "#2a9d8f" if overall_status == "PASS" else "#e76f51"
        fig.text(0.93, 0.95, overall_status, ha="right", fontsize=14, fontweight="bold", color=status_color)
        method = (
            "Direct source: MFC restart_data/ib_state_<step>.dat. The first record values are "
            "time, Fx, Fy, Fz. Forces are rotated into freestream drag/lift axes and normalized "
            "with q_inf = 0.5 rho_inf U_inf^2 = 4.5 and c = 1. No load is inferred from the "
            "512x512 CV tensors, and no pressure/viscous split is invented."
        )
        fig.text(0.07, 0.855, method, fontsize=9.5, wrap=True, va="top", linespacing=1.45)

        inventory_table = []
        for inventory in inventories:
            inventory_table.append(
                [
                    inventory.spec.case,
                    inventory.spec.stage,
                    inventory.status,
                    f"{len(inventory.records)}/{inventory.expected_count}",
                ]
            )
        axis = fig.add_axes([0.07, 0.49, 0.86, 0.30])
        axis.axis("off")
        table = axis.table(
            cellText=inventory_table,
            colLabels=["case", "source stage", "native-load status", "files"],
            loc="upper left",
            cellLoc="left",
            colLoc="left",
            colWidths=[0.20, 0.20, 0.43, 0.12],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(7.4)
        table.scale(1.0, 1.28)
        for (row, _column), cell in table.get_celld().items():
            cell.set_edgecolor("#ced4da")
            if row == 0:
                cell.set_facecolor("#e9ecef")
                cell.set_text_props(fontweight="bold")

        valid_summary = [row for row in summary if row["status"] == "VALID"]
        lines = ["Validated analysis windows:"]
        for row in valid_summary:
            lines.append(
                f"  {row['case']} / {row['window']}: "
                f"CL={float(row['CL_mean']):.5g} +/- {float(row['CL_temporal_std']):.3g}, "
                f"CD={float(row['CD_mean']):.5g} +/- {float(row['CD_temporal_std']):.3g}, "
                f"N={row['samples']}"
            )
        if not valid_summary:
            lines.append("  none - native records were missing, zero, corrupt, or too incomplete")
        fig.text(0.07, 0.39, "\n".join(lines), family="monospace", fontsize=8.1, va="top", linespacing=1.35)
        caveat = (
            "Interpretation guardrails: +/- is temporal standard deviation, not numerical "
            "uncertainty. The f180 Reynolds comparison uses the common 3<=t<=6 window. "
            "The mature Re=1e6 window (26<=t<=31) is reported separately. Missing restart "
            "intervals remain explicit and are never interpolated."
        )
        fig.text(0.07, 0.19, caveat, fontsize=9.2, va="top", wrap=True, linespacing=1.45)
        fig.text(0.07, 0.08, f"MFC schema provenance: {MFC_SCHEMA_URL}", fontsize=7.5, color="#495057", wrap=True)
        fig.text(0.93, 0.035, "1", ha="right", fontsize=8, color="#6c757d")
        pdf.savefig(fig)
        plt.close(fig)

        fig, axes = plt.subplots(2, 1, figsize=(8.27, 11.69), sharex=True, constrained_layout=True)
        for coefficient, axis in (("CL", axes[0]), ("CD", axes[1])):
            any_line = False
            for case in ("re1e4_f180", "re5e4_f180", "re1e5_f180"):
                any_line |= _plot_history(axis, history, case, coefficient, CASE_LABELS[case])
            _apply_plot_style(axis, rf"${coefficient}$")
            axis.set_xlim(0.0, 6.0)
            if any_line:
                axis.legend(frameon=False)
            else:
                axis.text(0.5, 0.5, "No usable native history", ha="center", va="center", transform=axis.transAxes)
        axes[0].set_title("Same-grid Reynolds effect: native total IB loads", loc="left", fontweight="bold")
        axes[1].set_xlabel(r"convective time $tU_\infty/c$")
        fig.suptitle("f180 comparison; common analysis window 3 <= t <= 6", fontsize=13)
        pdf.savefig(fig)
        plt.close(fig)

        fig, axes = plt.subplots(2, 1, figsize=(8.27, 11.69), sharex=True, constrained_layout=True)
        for coefficient, axis in (("CL", axes[0]), ("CD", axes[1])):
            any_line = False
            for case in ("re1e4_f180", "re1e4_f270"):
                any_line |= _plot_history(axis, history, case, coefficient, CASE_LABELS[case])
            _apply_plot_style(axis, rf"${coefficient}$")
            axis.set_xlim(0.0, 6.0)
            if any_line:
                axis.legend(frameon=False)
            else:
                axis.text(0.5, 0.5, "No usable native history", ha="center", va="center", transform=axis.transAxes)
        axes[0].set_title(r"Grid control at $Re_c=10^4$", loc="left", fontweight="bold")
        axes[1].set_xlabel(r"convective time $tU_\infty/c$")
        pdf.savefig(fig)
        plt.close(fig)

        fig, axes = plt.subplots(2, 1, figsize=(8.27, 11.69), sharex=True, constrained_layout=True)
        for coefficient, axis in (("CL", axes[0]), ("CD", axes[1])):
            plotted = _plot_history(axis, history, "re1e6_f270", coefficient, CASE_LABELS["re1e6_f270"])
            _apply_plot_style(axis, rf"${coefficient}$")
            if plotted:
                axis.legend(frameon=False)
            else:
                axis.text(0.5, 0.5, "No usable native history", ha="center", va="center", transform=axis.transAxes)
        axes[0].set_title(r"Available native $Re_c=10^6$ load segments", loc="left", fontweight="bold")
        axes[1].set_xlabel(r"convective time $tU_\infty/c$")
        fig.suptitle("Line breaks are missing/pruned intervals; no interpolation", fontsize=12)
        pdf.savefig(fig)
        plt.close(fig)

        fig = plt.figure(figsize=(8.27, 11.69))
        table_axis = fig.add_axes([0.035, 0.63, 0.93, 0.27])
        table_axis.axis("off")
        table_rows = []
        for row in summary:
            table_rows.append(
                [
                    row["case"],
                    row["window"],
                    row["status"],
                    str(row["samples"]),
                    _format_value(row.get("CL_mean")),
                    _format_value(row.get("CL_temporal_std"), 3),
                    _format_value(row.get("CD_mean")),
                    _format_value(row.get("CD_temporal_std"), 3),
                    _format_value(row.get("L_over_D_from_means"), 3),
                ]
            )
        table = table_axis.table(
            cellText=table_rows,
            colLabels=["case", "window", "status", "N", "mean CL", "std CL", "mean CD", "std CD", "L/D"],
            loc="upper center",
            cellLoc="center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(7.5)
        table.scale(1.0, 1.45)
        for (row, _column), cell in table.get_celld().items():
            cell.set_edgecolor("#ced4da")
            if row == 0:
                cell.set_facecolor("#e9ecef")
                cell.set_text_props(fontweight="bold")
        fig.suptitle("Auditable native-force statistics", fontsize=15, fontweight="bold", y=0.955)

        valid_rows = [row for row in summary if row["status"] == "VALID"]
        short_labels = []
        for row in valid_rows:
            label = row["case"].replace("re", "Re=").replace("_", "\n")
            if row["window"] == "mature_t26_t31":
                label = "Re=1e6 f270\nt=26..31"
            short_labels.append(label)
        x_values = np.arange(len(valid_rows))
        for coefficient, position in (("CL", [0.085, 0.18, 0.38, 0.31]), ("CD", [0.555, 0.18, 0.38, 0.31])):
            chart = fig.add_axes(position)
            if valid_rows:
                means = [float(row[f"{coefficient}_mean"]) for row in valid_rows]
                errors = [float(row[f"{coefficient}_temporal_std"]) for row in valid_rows]
                colors = [COLORS[str(row["case"])] for row in valid_rows]
                chart.bar(x_values, means, yerr=errors, capsize=3, color=colors, alpha=0.90)
                chart.set_xticks(x_values)
                chart.set_xticklabels(short_labels, fontsize=6.8)
            else:
                chart.text(0.5, 0.5, "No valid windows", ha="center", va="center", transform=chart.transAxes)
            chart.set_ylabel(rf"mean ${coefficient}$ (bars: temporal std.)")
            chart.grid(True, axis="y", alpha=0.22)
            chart.spines[["top", "right"]].set_visible(False)

        boundary_text = (
            "Boundary audit: " + ", ".join(
                f"{row['boundary_step']}={row['status']}" for row in continuity
            )
            if continuity
            else "Boundary audit: not applicable"
        )
        fig.text(0.06, 0.105, boundary_text, fontsize=7.8, va="top", wrap=True)
        fig.text(
            0.06,
            0.065,
            "Comparisons: " + "; ".join(
                f"{row['comparison']}={row['status']}" for row in comparisons
            ),
            fontsize=7.8,
            va="top",
            wrap=True,
        )
        pdf.savefig(fig)
        plt.close(fig)


def _inventory_row(inventory: SourceInventory) -> dict[str, Any]:
    spec = inventory.spec
    return {
        "case": spec.case,
        "Re_c": spec.reynolds,
        "grid": spec.grid,
        "role": spec.role,
        "stage": spec.stage,
        "case_dir": str(spec.case_dir.resolve()),
        "dt": spec.dt,
        "start_step": spec.start_step,
        "stop_step": spec.stop_step,
        "save_stride": spec.save_stride,
        "expected_files": inventory.expected_count,
        "found_expected_files": len(inventory.records),
        "missing_files": len(inventory.missing_steps),
        "missing_steps": ",".join(str(step) for step in inventory.missing_steps),
        "parse_errors": " | ".join(inventory.parse_errors),
        "time_mismatches": " | ".join(inventory.time_mismatches),
        "per_process_files": inventory.per_process_files,
        "maximum_noninitial_force": inventory.maximum_noninitial_force,
        "status": inventory.status,
        "usable": inventory.usable,
    }


def _determine_status(
    inventories: Sequence[SourceInventory],
    continuity: Sequence[dict[str, Any]],
    summary: Sequence[dict[str, Any]],
) -> str:
    valid_windows = sum(row["status"] == "VALID" for row in summary)
    if valid_windows == 0:
        return "FAILED"
    all_sources_valid = all(inventory.status == "VALID" for inventory in inventories)
    all_boundaries_pass = all(row["status"] == "PASS" for row in continuity)
    all_windows_valid = all(row["status"] == "VALID" for row in summary)
    return "PASS" if all_sources_valid and all_boundaries_pass and all_windows_valid else "PARTIAL"


def _write_readme(
    path: Path,
    status: str,
    inventories: Sequence[SourceInventory],
    summary: Sequence[dict[str, Any]],
    archive: Path | None,
) -> None:
    unavailable = [
        f"- `{item.spec.case}/{item.spec.stage}`: `{item.status}`"
        for item in inventories
        if item.status != "VALID"
    ]
    valid_lines = []
    for row in summary:
        if row["status"] == "VALID":
            valid_lines.append(
                f"- `{row['case']}` `{row['window']}`: "
                f"CL={float(row['CL_mean']):.6g} (std {float(row['CL_temporal_std']):.3g}), "
                f"CD={float(row['CD_mean']):.6g} (std {float(row['CD_temporal_std']):.3g}), "
                f"N={row['samples']}."
            )
    text = f"""# Read me first - native MFC forces

Overall status: **{status}**

This package extracts total immersed-boundary force directly from the native MFC
`restart_data/ib_state_<step>.dat` records. It does not use the 512x512 machine-vision
tensors to infer forces.

## Primary files

- `TIM_COLONIUS_REYNOLDS_FORCES.png`: compact figure suitable for email.
- `TIM_COLONIUS_NATIVE_FORCES.pdf`: full audit and plots.
- `native_force_history.csv`: merged, gap-preserving coefficient history.
- `native_force_raw_history.csv`: every retained native source record before boundary deduplication.
- `native_force_summary.csv` / `.json`: means, temporal standard deviations, slopes, and coverage.
- `native_force_source_inventory.csv`: missing/zero/corrupt source audit.
- `native_force_continuity.csv`: restart-boundary identity audit.
- `native_force_comparisons.csv`: same-grid Reynolds and fixed-Re grid comparisons.

## Definitions

`drag = Fx cos(alpha) + Fy sin(alpha)` and
`lift = -Fx sin(alpha) + Fy cos(alpha)`, with alpha=40 deg.
`CD = drag/(q_inf c)` and `CL = lift/(q_inf c)`, where rho_inf=1,
U_inf=3, q_inf=4.5, and c=1.

The MFC binary schema is pinned to `{MFC_SOURCE_REV}`:
{MFC_SCHEMA_URL}

## Valid windows

{os.linesep.join(valid_lines) if valid_lines else '- None.'}

## Missing or limited sources

{os.linesep.join(unavailable) if unavailable else '- None.'}

## Scientific limits

- The files provide total native IB load; they do not separate pressure and viscous load.
- Temporal standard deviation is flow variability, not numerical uncertainty.
- Reynolds comparisons use f180 and 3<=t<=6. The mature Re=1e6 window 26<=t<=31
  is deliberately reported separately.
- Missing/pruned intervals are not interpolated.
- A zero native history is rejected as unusable, because a prior f180 run exhibited this failure mode.

Archive: `{archive if archive is not None else 'not requested'}`
"""
    path.write_text(text, encoding="utf-8")


def _write_checksums(output: Path, names: Sequence[str]) -> None:
    lines = []
    for name in names:
        payload = (output / name).read_bytes()
        lines.append(f"{hashlib.sha256(payload).hexdigest()}  {name}")
    (output / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_archive(output: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_suffix(archive.suffix + ".partial")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
        for path in sorted(output.iterdir()):
            if path.is_file() and path.name not in {"analysis.log", "ANALYSIS_FAILED.txt"}:
                handle.write(path, arcname=path.name)
    temporary.replace(archive)


def build_default_specs(args: argparse.Namespace) -> list[SourceSpec]:
    re1e4_root = args.re1e4_root.expanduser().resolve()
    ladder_root = args.ladder_root.expanduser().resolve()
    initial = args.re1e6_initial.expanduser().resolve()
    chain = args.long_chain.expanduser().resolve()
    specs = [
        SourceSpec("re1e4_f180", 1.0e4, "f180", "grid_control", "t00_t06", re1e4_root / "f180", 1.0 / 3600.0, 0, 21600, 180, 0),
        SourceSpec("re1e4_f270", 1.0e4, "f270", "primary", "t00_t06", re1e4_root / "f270", 1.0 / 5400.0, 0, 32400, 270, 0),
        SourceSpec("re5e4_f180", 5.0e4, "f180", "screening", "t00_t06", ladder_root / "re5e4", 1.0 / 3600.0, 0, 21600, 360, 0),
        SourceSpec("re1e5_f180", 1.0e5, "f180", "screening", "t00_t06", ladder_root / "re1e5", 1.0 / 3600.0, 0, 21600, 360, 0),
        SourceSpec("re1e6_f270", 1.0e6, "f270", "primary", "t00_t06", initial, 1.0 / 5400.0, 0, 32400, 270, 0),
    ]
    stages = (
        ("t06_t11", 32400, 59400),
        ("t11_t16", 59400, 86400),
        ("t16_t21", 86400, 113400),
        ("t21_t26", 113400, 140400),
        ("t26_t31", 140400, 167400),
    )
    for order, (stage, start, stop) in enumerate(stages, start=1):
        specs.append(
            SourceSpec(
                "re1e6_f270", 1.0e6, "f270", "long_baseline", stage,
                chain / stage, 1.0 / 5400.0, start, stop, 270, order
            )
        )
    return specs


def run_analysis(specs: Sequence[SourceSpec], output: Path, archive: Path | None) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    inventories = [scan_source(spec) for spec in specs]
    continuity = boundary_audit(inventories)
    raw_history = [row for item in inventories for row in item.records]
    history = merge_histories(inventories)
    summary = summarize_windows(history)
    comparisons = build_comparisons(summary)
    status = _determine_status(inventories, continuity, summary)

    inventory_rows = [_inventory_row(item) for item in inventories]
    inventory_fields = tuple(inventory_rows[0])
    continuity_fields = (
        tuple(continuity[0]) if continuity else (
            "case", "boundary_step", "boundary_time", "left_stage", "right_stage",
            "left_file", "right_file", "status", "delta_time", "delta_force_x",
            "delta_force_y", "delta_CD", "delta_CL",
        )
    )
    comparison_fields = tuple(comparisons[0])

    _write_csv(output / "native_force_raw_history.csv", raw_history, HISTORY_FIELDS)
    _write_csv(output / "native_force_history.csv", history, HISTORY_FIELDS)
    _write_csv(output / "native_force_source_inventory.csv", inventory_rows, inventory_fields)
    _write_csv(output / "native_force_continuity.csv", continuity, continuity_fields)
    _write_csv(output / "native_force_summary.csv", summary, SUMMARY_FIELDS)
    _write_csv(output / "native_force_comparisons.csv", comparisons, comparison_fields)

    report = {
        "status": status,
        "normalization": {
            "alpha_deg": ALPHA_DEG,
            "rho_inf": RHO_INF,
            "U_inf": U_INF,
            "q_inf": Q_INF,
            "chord": CHORD,
            "force_source": "native MFC global ib_state records",
            "force_content": "total immersed-boundary force",
            "pressure_viscous_split_available": False,
        },
        "mfc_schema": {"revision": MFC_SOURCE_REV, "url": MFC_SCHEMA_URL},
        "inventory": inventory_rows,
        "continuity": list(continuity),
        "windows": list(summary),
        "comparisons": list(comparisons),
        "merged_records": len(history),
        "raw_records": len(raw_history),
    }
    (output / "native_force_summary.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )

    render_email_figure(
        output / "TIM_COLONIUS_REYNOLDS_FORCES.png", history, summary, status
    )
    render_pdf(
        output / "TIM_COLONIUS_NATIVE_FORCES.pdf",
        history,
        inventories,
        continuity,
        summary,
        comparisons,
        status,
    )
    _write_readme(output / "READ_ME_FIRST.md", status, inventories, summary, archive)

    marker = output / "ANALYSIS_COMPLETE.txt"
    marker.write_text(
        "\n".join(
            (
                f"status={status}",
                f"native_raw_records={len(raw_history)}",
                f"native_merged_records={len(history)}",
                f"valid_windows={sum(row['status'] == 'VALID' for row in summary)}",
                f"archive={archive if archive is not None else ''}",
            )
        ) + "\n",
        encoding="utf-8",
    )

    names = (
        "READ_ME_FIRST.md",
        "TIM_COLONIUS_NATIVE_FORCES.pdf",
        "TIM_COLONIUS_REYNOLDS_FORCES.png",
        "native_force_raw_history.csv",
        "native_force_history.csv",
        "native_force_source_inventory.csv",
        "native_force_continuity.csv",
        "native_force_summary.csv",
        "native_force_summary.json",
        "native_force_comparisons.csv",
        "ANALYSIS_COMPLETE.txt",
    )
    _write_checksums(output, names)
    if archive is not None:
        _make_archive(output, archive)
    if status == "FAILED":
        raise RuntimeError("no force window passed native-load validation")
    return report


def _write_test_record(path: Path, time_value: float, cd: float, cl: float) -> None:
    alpha = math.radians(ALPHA_DEG)
    drag = cd * Q_INF * CHORD
    lift = cl * Q_INF * CHORD
    force_x = drag * math.cos(alpha) - lift * math.sin(alpha)
    force_y = drag * math.sin(alpha) + lift * math.cos(alpha)
    values = [time_value, force_x, force_y, 0.0] + [0.0] * 16
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack(f"={RECORD_WIDTH}d", *values))


def self_test() -> None:
    drag, lift, cd, cl = force_coefficients(4.5, 0.0)
    assert math.isclose(drag, 4.5 * math.cos(math.radians(ALPHA_DEG)))
    assert math.isfinite(lift) and math.isfinite(cd) and math.isfinite(cl)

    retained_root = os.environ.get("MFC_NATIVE_FORCE_SELF_TEST_OUTPUT")
    context = (
        nullcontext(Path(retained_root).expanduser().resolve())
        if retained_root
        else tempfile.TemporaryDirectory(prefix="mfc-native-force-test-")
    )
    with context as temporary:
        root = Path(temporary)
        root.mkdir(parents=True, exist_ok=True)
        specs: list[SourceSpec] = []
        synthetic = (
            ("re1e4_f180", 1.0e4, "f180", 0.84, 0.94),
            ("re1e4_f270", 1.0e4, "f270", 0.83, 0.95),
            ("re5e4_f180", 5.0e4, "f180", 0.78, 1.02),
            ("re1e5_f180", 1.0e5, "f180", 0.74, 1.08),
            ("re1e6_f270", 1.0e6, "f270", 0.70, 1.15),
        )
        for index, (case, reynolds, grid, cd_mean, cl_mean) in enumerate(synthetic):
            directory = root / case / "initial"
            spec = SourceSpec(case, reynolds, grid, "test", "t00_t06", directory, 0.5, 0, 12, 1, 0)
            specs.append(spec)
            for step in range(13):
                _write_test_record(
                    directory / "restart_data" / f"ib_state_{step}.dat",
                    0.5 * step,
                    cd_mean + 0.01 * math.sin(step),
                    cl_mean + 0.015 * math.cos(step),
                )
        mature_dir = root / "re1e6_f270" / "mature"
        mature = SourceSpec("re1e6_f270", 1.0e6, "f270", "test", "t26_t31", mature_dir, 0.5, 52, 62, 1, 1)
        specs.append(mature)
        for step in range(52, 63):
            _write_test_record(
                mature_dir / "restart_data" / f"ib_state_{step}.dat",
                0.5 * step,
                0.72 + 0.01 * math.sin(step),
                1.18 + 0.02 * math.cos(step),
            )

        zero_dir = root / "zero"
        zero_spec = SourceSpec("zero", 1.0, "test", "test", "zero", zero_dir, 1.0, 0, 2, 1, 0)
        for step in range(3):
            _write_test_record(zero_dir / "restart_data" / f"ib_state_{step}.dat", float(step), 0.0, 0.0)
        assert scan_source(zero_spec).status == "ZERO_NATIVE_LOADS"

        first_inventory = scan_source(specs[0])
        assert first_inventory.status == "VALID"
        first_row = first_inventory.records[2]
        assert math.isclose(float(first_row["CD"]), 0.84 + 0.01 * math.sin(2), rel_tol=1.0e-12)
        assert math.isclose(float(first_row["CL"]), 0.94 + 0.015 * math.cos(2), rel_tol=1.0e-12)

        left = dict(first_inventory.records[-1])
        right = dict(left)
        assert records_equivalent(left, right)
        right["force_x"] = float(right["force_x"]) + 1.0e-3
        assert not records_equivalent(left, right)

        boundary_left_dir = root / "boundary" / "left"
        boundary_right_dir = root / "boundary" / "right"
        boundary_left = SourceSpec(
            "re1e6_f270", 1.0e6, "f270", "test", "left", boundary_left_dir,
            1.0, 0, 1, 1, 0
        )
        boundary_right = SourceSpec(
            "re1e6_f270", 1.0e6, "f270", "test", "right", boundary_right_dir,
            1.0, 1, 2, 1, 1
        )
        for step in (0, 1):
            _write_test_record(
                boundary_left_dir / "restart_data" / f"ib_state_{step}.dat",
                float(step), 0.8, 1.0
            )
        for step in (1, 2):
            _write_test_record(
                boundary_right_dir / "restart_data" / f"ib_state_{step}.dat",
                float(step), 0.8, 1.0
            )
        audit = boundary_audit([scan_source(boundary_left), scan_source(boundary_right)])
        assert len(audit) == 1 and audit[0]["status"] == "PASS"
        _write_test_record(
            boundary_right_dir / "restart_data/ib_state_1.dat", 1.0, 0.9, 1.0
        )
        audit = boundary_audit([scan_source(boundary_left), scan_source(boundary_right)])
        assert len(audit) == 1 and audit[0]["status"] == "FAIL_MISMATCH"

        output = root / "output"
        archive = root / "native_force_test.zip"
        report = run_analysis(specs, output, archive)
        assert report["status"] == "PASS", json.dumps(report, indent=2)
        assert (output / "TIM_COLONIUS_NATIVE_FORCES.pdf").stat().st_size > 10_000
        assert (output / "TIM_COLONIUS_REYNOLDS_FORCES.png").stat().st_size > 10_000
        assert archive.stat().st_size > 10_000
        with (output / "native_force_history.csv").open(newline="", encoding="utf-8") as stream:
            history_rows = list(csv.DictReader(stream))
        re1e6_segments = {
            row["segment_id"] for row in history_rows if row["case"] == "re1e6_f270"
        }
        assert len(re1e6_segments) == 2
        with zipfile.ZipFile(archive) as handle:
            assert "native_force_history.csv" in handle.namelist()
    print("MFC_NATIVE_FORCE_SELF_TEST=PASS")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--re1e4-root", type=Path)
    parser.add_argument("--ladder-root", type=Path)
    parser.add_argument("--re1e6-initial", type=Path)
    parser.add_argument("--long-chain", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if not args.self_test:
        required = (
            "re1e4_root", "ladder_root", "re1e6_initial", "long_chain", "output"
        )
        missing = [name.replace("_", "-") for name in required if getattr(args, name) is None]
        if missing:
            parser.error("missing required arguments: " + ", ".join("--" + name for name in missing))
    return args


def main() -> int:
    args = parse_arguments()
    if args.self_test:
        self_test()
        return 0
    assert args.output is not None
    specs = build_default_specs(args)
    report = run_analysis(specs, args.output.expanduser().resolve(), args.archive)
    print(json.dumps({
        "status": report["status"],
        "raw_records": report["raw_records"],
        "merged_records": report["merged_records"],
        "valid_windows": sum(row["status"] == "VALID" for row in report["windows"]),
    }, indent=2))
    print(f"OUTPUT={args.output.expanduser().resolve()}")
    if args.archive is not None:
        print(f"ARCHIVE={args.archive.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
