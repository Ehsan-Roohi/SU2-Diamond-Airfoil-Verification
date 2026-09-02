#!/usr/bin/env python3
"""Aggregate per-case MFC force/shock diagnostics into paper-facing products."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mfc-reynolds-t31-aggregate")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


PRIMARY_LABELS = ("re1e4_f270", "re5e4_f180", "re1e5_f180", "re1e6_f270")
COLORS = {
    "re1e4_f180": "#6c757d",
    "re1e4_f270": "#264653",
    "re5e4_f180": "#2a9d8f",
    "re1e5_f180": "#e9c46a",
    "re1e6_f270": "#e76f51",
    "re1e6_long_t31": "#8f2d56",
}


@dataclass(frozen=True)
class CaseInfo:
    label: str
    display: str
    reynolds: float
    grid: str
    dt: float
    case_dir: Path
    analysis_start: float
    role: str


def read_case_table(path: Path) -> list[CaseInfo]:
    result: list[CaseInfo] = []
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            result.append(
                CaseInfo(
                    label=row["label"],
                    display=row["display"],
                    reynolds=float(row["reynolds"]),
                    grid=row["grid"],
                    dt=float(row["dt"]),
                    case_dir=Path(row["case_dir"]),
                    analysis_start=float(row["analysis_start"]),
                    role=row["role"],
                )
            )
    labels = [row.label for row in result]
    required = set(PRIMARY_LABELS) | {"re1e4_f180", "re1e6_long_t31"}
    missing = sorted(required - set(labels))
    if len(labels) != len(set(labels)):
        raise RuntimeError("duplicate labels in case table")
    if missing:
        raise RuntimeError(f"case table lacks required labels: {missing}")
    return result


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def number(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def safe_relative(first: float, second: float) -> float:
    scale = max(abs(first), abs(second), 1.0e-12)
    return abs(first - second) / scale


def spectrum(time: np.ndarray, values: np.ndarray) -> dict[str, float | str | int | None]:
    mask = np.isfinite(time) & np.isfinite(values)
    time = time[mask]
    values = values[mask]
    if len(time) < 16:
        return {"status": "INSUFFICIENT_SAMPLES", "samples": len(time), "frequency": None, "Strouhal": None}
    order = np.argsort(time)
    time = time[order]
    values = values[order]
    spacing = float(np.median(np.diff(time)))
    if spacing <= 0 or not np.allclose(np.diff(time), spacing, rtol=1.0e-5, atol=1.0e-10):
        return {"status": "NONUNIFORM_TIME", "samples": len(time), "frequency": None, "Strouhal": None}
    centered_time = time - np.mean(time)
    slope, intercept = np.polyfit(centered_time, values, 1)
    detrended = values - (slope * centered_time + intercept)
    transformed = np.fft.rfft(detrended * np.hanning(len(detrended)))
    frequency = np.fft.rfftfreq(len(detrended), spacing)
    power = np.abs(transformed) ** 2
    if len(power) <= 1 or np.nanmax(power[1:]) <= 0:
        return {"status": "NO_PEAK", "samples": len(time), "frequency": None, "Strouhal": None}
    index = int(np.argmax(power[1:]) + 1)
    peak_frequency = float(frequency[index])
    duration = float(time[-1] - time[0])
    cycles = peak_frequency * duration
    return {
        "status": "RESOLVED_5_CYCLES" if cycles >= 5.0 else "PRELIMINARY_FEWER_THAN_5_CYCLES",
        "samples": len(time),
        "frequency": peak_frequency,
        "Strouhal": peak_frequency / 3.0,
        "cycles": cycles,
        "duration": duration,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def metric_value(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def per_case_summary(case: CaseInfo, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": case.label,
        "display": case.display,
        "Re_c": case.reynolds,
        "grid": case.grid,
        "time_end": max(payload.get("saved_steps", [0])) * case.dt,
        "analysis_start": payload.get("statistical_window", [None, None])[0],
        "samples": metric_value(payload, "force_statistics", "CL", "samples"),
        "force_source": payload.get("force_source"),
        "force_assessment": payload.get("force_source_assessment"),
        "CL_mean": metric_value(payload, "force_statistics", "CL", "mean"),
        "CL_rms": metric_value(payload, "force_statistics", "CL", "rms_fluctuation"),
        "CL_ci95_half_width": metric_value(payload, "force_statistics", "CL", "ci95_mean"),
        "CL_pressure_mean": metric_value(payload, "force_statistics", "CL_pressure", "mean"),
        "CL_viscous_mean": metric_value(payload, "force_statistics", "CL_viscous", "mean"),
        "CD_mean": metric_value(payload, "force_statistics", "CD", "mean"),
        "CD_rms": metric_value(payload, "force_statistics", "CD", "rms_fluctuation"),
        "CD_ci95_half_width": metric_value(payload, "force_statistics", "CD", "ci95_mean"),
        "CD_pressure_mean": metric_value(payload, "force_statistics", "CD_pressure", "mean"),
        "CD_viscous_mean": metric_value(payload, "force_statistics", "CD_viscous", "mean"),
        "dominant_frequency": metric_value(payload, "shedding", "dominant_frequency"),
        "Strouhal": metric_value(payload, "shedding", "strouhal"),
        "spectrum_status": metric_value(payload, "shedding", "status"),
        "shock_standoff_mean": metric_value(payload, "shock_statistics", "stand_off_over_c", "mean"),
        "shock_angle_mean_deg": metric_value(payload, "shock_statistics", "shock_angle_to_freestream_deg", "mean"),
        "diagnostic_status": payload.get("status"),
    }


def plot_histories(output: Path, cases: list[CaseInfo], histories: dict[str, list[dict[str, str]]]) -> None:
    selected = [case for case in cases if case.label != "re1e6_long_t31"]
    fig, axes = plt.subplots(2, 1, figsize=(11.5, 8.4), sharex=True, constrained_layout=True)
    for case in selected:
        rows = histories[case.label]
        time = np.asarray([number(row, "time") for row in rows])
        style = "--" if case.label == "re1e4_f180" else "-"
        axes[0].plot(time, [number(row, "CL") for row in rows], style, color=COLORS[case.label], lw=1.45, label=f"{case.display}, {case.grid}")
        axes[1].plot(time, [number(row, "CD") for row in rows], style, color=COLORS[case.label], lw=1.45)
    axes[0].set_ylabel(r"$C_L$")
    axes[1].set_ylabel(r"$C_D$")
    axes[1].set_xlabel(r"Convective time $tU_\infty/c$")
    axes[0].legend(ncol=2, fontsize=9)
    axes[0].set_title("Reynolds-number screening: reconstructed force histories")
    for ax in axes:
        ax.grid(alpha=0.25)
        ax.axvspan(0.0, 3.0, color="0.85", alpha=0.35, label="excluded transient" if ax is axes[0] else None)
    fig.savefig(output / "reynolds_force_history_comparison.png", dpi=240)
    plt.close(fig)


def plot_spectra(output: Path, cases: list[CaseInfo], spectra: dict[str, list[dict[str, str]]]) -> None:
    fig, ax = plt.subplots(figsize=(10.5, 6.5), constrained_layout=True)
    for case in cases:
        if case.label == "re1e6_long_t31":
            continue
        rows = spectra.get(case.label, [])
        if not rows:
            continue
        st = np.asarray([number(row, "strouhal") for row in rows])
        power = np.asarray([number(row, "power") for row in rows])
        mask = np.isfinite(st) & np.isfinite(power) & (st > 0)
        if not np.any(mask):
            continue
        normalized = power[mask] / max(float(np.nanmax(power[mask])), 1.0e-30)
        style = "--" if case.label == "re1e4_f180" else "-"
        ax.semilogy(st[mask], np.maximum(normalized, 1.0e-10), style, color=COLORS[case.label], lw=1.5, label=f"{case.display}, {case.grid}")
    ax.set(xlabel=r"Strouhal number $St=fc/U_\infty$", ylabel="Normalized lift-spectrum power", xlim=(0, 1.5))
    ax.grid(alpha=0.25)
    ax.legend(ncol=2, fontsize=9)
    ax.set_title("Short-record lift spectra (screening only)")
    fig.savefig(output / "reynolds_lift_spectrum_comparison.png", dpi=240)
    plt.close(fig)


def plot_force_components(output: Path, summaries: list[dict[str, Any]]) -> None:
    selected = [row for row in summaries if row["label"] != "re1e6_long_t31"]
    positions = np.arange(len(selected), dtype=float)
    labels = [f"{row['display']}\n{row['grid']}" for row in selected]
    fig, axes = plt.subplots(2, 1, figsize=(12.0, 8.5), sharex=True, constrained_layout=True)
    for ax, coefficient in zip(axes, ("CL", "CD")):
        pressure = np.asarray([
            float(row[f"{coefficient}_pressure_mean"])
            if row.get(f"{coefficient}_pressure_mean") is not None
            else math.nan
            for row in selected
        ])
        viscous = np.asarray([
            float(row[f"{coefficient}_viscous_mean"])
            if row.get(f"{coefficient}_viscous_mean") is not None
            else math.nan
            for row in selected
        ])
        total = np.asarray([float(row[f"{coefficient}_mean"]) for row in selected])
        ax.bar(positions - 0.18, pressure, width=0.36, color="#457b9d", label="pressure")
        ax.bar(positions + 0.18, viscous, width=0.36, color="#e76f51", label="viscous")
        ax.plot(positions, total, "ko", ms=5, label="total")
        ax.axhline(0.0, color="0.3", lw=0.7)
        ax.set_ylabel(rf"Mean ${coefficient}$ contribution")
        ax.grid(alpha=0.2, axis="y")
    axes[0].legend(ncol=3, fontsize=9)
    axes[1].set_xticks(positions, labels)
    fig.suptitle("Pressure and viscous contributions to reconstructed aerodynamic coefficients", fontsize=14, fontweight="bold")
    fig.savefig(output / "reynolds_force_component_comparison.png", dpi=240, facecolor="white")
    plt.close(fig)


def plot_shock(output: Path, cases: list[CaseInfo], shocks: dict[str, list[dict[str, str]]]) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(11.5, 8.4), sharex=True, constrained_layout=True)
    for case in cases:
        if case.label == "re1e6_long_t31":
            continue
        rows = shocks[case.label]
        time = np.asarray([number(row, "time") for row in rows])
        stand = np.asarray([number(row, "stand_off_over_c") for row in rows])
        angle = np.asarray([number(row, "shock_angle_to_freestream_deg") for row in rows])
        style = "--" if case.label == "re1e4_f180" else "-"
        axes[0].plot(time, stand, style, color=COLORS[case.label], lw=1.35, label=f"{case.display}, {case.grid}")
        axes[1].plot(time, angle, style, color=COLORS[case.label], lw=1.35)
    axes[0].set_ylabel(r"Bow-shock stand-off $d_s/c$")
    axes[1].set_ylabel("Local shock angle (deg)")
    axes[1].set_xlabel(r"Convective time $tU_\infty/c$")
    axes[0].legend(ncol=2, fontsize=9)
    axes[0].set_title("Reynolds-number dependence of the bow shock")
    for ax in axes:
        ax.grid(alpha=0.25)
    fig.savefig(output / "reynolds_shock_history_comparison.png", dpi=240)
    plt.close(fig)


def long_window_analysis(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    time = np.asarray([number(row, "time") for row in rows])
    cl = np.asarray([number(row, "CL") for row in rows])
    cd = np.asarray([number(row, "CD") for row in rows])
    if len(time) < 32 or not all(np.isfinite(values).all() for values in (time, cl, cd)):
        raise RuntimeError("long force history must contain at least 32 finite samples")
    order = np.argsort(time)
    time, cl, cd = time[order], cl[order], cd[order]
    if np.any(np.diff(time) <= 0.0):
        raise RuntimeError("long force history has a duplicated or reversed time base")
    windows: list[dict[str, Any]] = []
    for start in (6.0, 11.0, 16.0, 21.0, 26.0):
        end = start + 5.0
        upper = time <= end + 1.0e-10 if math.isclose(end, 31.0) else time < end - 1.0e-10
        mask = (time >= start - 1.0e-10) & upper
        sample_count = int(np.sum(mask))
        minimum = 8 if math.isclose(start, 21.0) else 16
        if sample_count < minimum:
            raise RuntimeError(f"too few long-run samples in window {start:g}..{end:g}")
        if math.isclose(start, 21.0):
            spec: dict[str, Any] = {
                "status": "SPARSE_RETENTION_NO_SPECTRAL_CLAIM",
                "samples": sample_count,
                "frequency": None,
                "Strouhal": None,
                "cycles": None,
            }
        else:
            spec = spectrum(time[mask], cl[mask])
        windows.append(
            {
                "window_start": start,
                "window_end": end,
                "samples": sample_count,
                "sampling": (
                    "SPARSE_RETAINED_NO_INTERPOLATION"
                    if math.isclose(start, 21.0)
                    else "DENSE_RETAINED_OR_VALIDATED_DERIVED"
                ),
                "CL_mean": float(np.nanmean(cl[mask])),
                "CL_rms": float(np.nanstd(cl[mask])),
                "CD_mean": float(np.nanmean(cd[mask])),
                "CD_rms": float(np.nanstd(cd[mask])),
                "dominant_frequency": spec.get("frequency"),
                "Strouhal": spec.get("Strouhal"),
                "resolved_cycles": spec.get("cycles"),
                "spectrum_status": spec.get("status"),
            }
        )
    continuity: list[dict[str, Any]] = []
    for boundary in (6.0, 11.0, 16.0, 21.0, 26.0):
        boundary_index = int(np.argmin(np.abs(time - boundary)))
        if not math.isclose(time[boundary_index], boundary, abs_tol=1.0e-8):
            continuity.append(
                {
                    "boundary_time": boundary,
                    "status": "BOUNDARY_SAMPLE_NOT_RETAINED",
                }
            )
            continue
        before_index = boundary_index - 1
        after_index = boundary_index + 1
        if before_index < 0 or after_index >= len(time):
            continuity.append(
                {
                    "boundary_time": boundary,
                    "status": "NOT_EVALUABLE_AT_RETAINED_HISTORY_EDGE",
                }
            )
            continue
        local = (time >= boundary - 0.5) & (time <= boundary + 0.5)
        local_cl_diff = np.abs(np.diff(cl[local]))
        local_cd_diff = np.abs(np.diff(cd[local]))
        cl_before = float(cl[boundary_index] - cl[before_index])
        cl_after = float(cl[after_index] - cl[boundary_index])
        cd_before = float(cd[boundary_index] - cd[before_index])
        cd_after = float(cd[after_index] - cd[boundary_index])
        continuity.append(
            {
                "boundary_time": boundary,
                "status": "EVALUATED_NEAREST_RETAINED_NEIGHBORS",
                "time_before": float(time[before_index]),
                "time_after": float(time[after_index]),
                "CL_two_sample_jump": float(cl[after_index] - cl[before_index]),
                "CD_two_sample_jump": float(cd[after_index] - cd[before_index]),
                "CL_increment_before": cl_before,
                "CL_increment_after": cl_after,
                "CD_increment_before": cd_before,
                "CD_increment_after": cd_after,
                "CL_increment_mismatch": cl_after - cl_before,
                "CD_increment_mismatch": cd_after - cd_before,
                "CL_mismatch_over_local_median_increment": float(abs(cl_after - cl_before) / max(np.median(local_cl_diff), 1e-12)),
                "CD_mismatch_over_local_median_increment": float(abs(cd_after - cd_before) / max(np.median(local_cd_diff), 1e-12)),
            }
        )
    return windows, continuity


def plot_long(output: Path, rows: list[dict[str, str]], windows: list[dict[str, Any]]) -> None:
    time = np.asarray([number(row, "time") for row in rows])
    cl = np.asarray([number(row, "CL") for row in rows])
    cd = np.asarray([number(row, "CD") for row in rows])
    fig, axes = plt.subplots(3, 1, figsize=(12.0, 10.0), sharex=True, constrained_layout=True)
    axes[0].plot(time, cl, color=COLORS["re1e6_long_t31"], lw=1.0)
    axes[1].plot(time, cd, color="#277da1", lw=1.0)
    centers = [(row["window_start"] + row["window_end"]) / 2 for row in windows]
    axes[2].plot(centers, [row["CL_rms"] for row in windows], "o-", color=COLORS["re1e6_long_t31"], label=r"$C_L$ RMS")
    axes[2].plot(centers, [row["CD_rms"] for row in windows], "s-", color="#277da1", label=r"$C_D$ RMS")
    axes[0].set_ylabel(r"$C_L$")
    axes[1].set_ylabel(r"$C_D$")
    axes[2].set_ylabel("Five-time-unit RMS")
    axes[2].set_xlabel(r"Convective time $tU_\infty/c$")
    axes[2].legend()
    for ax in axes:
        ax.grid(alpha=0.25)
        for boundary in (6, 11, 16, 21, 26, 31):
            ax.axvline(boundary, color="0.55", lw=0.7, ls=":")
    axes[0].set_title(r"Long HLL baseline, $Re_c=10^6$: force stationarity through $t=31$")
    fig.savefig(output / "hll_t31_stationarity_and_restart_boundaries.png", dpi=240)
    plt.close(fig)


def plot_summary_metrics(
    output: Path,
    summaries: list[dict[str, Any]],
    field_payload: dict[str, Any],
) -> None:
    summary = {row["label"]: row for row in summaries}
    fields = {row["label"]: row for row in field_payload.get("metrics", [])}
    panels = (
        ("CL_mean", r"Mean $C_L$", summary),
        ("CD_mean", r"Mean $C_D$", summary),
        ("CL_rms", r"RMS $C_L'$", summary),
        ("shock_standoff_mean", r"Bow-shock $d_s/c$", summary),
        ("centerline_reverse_flow_extent_over_c", r"Reverse-flow extent $s/c$", fields),
        ("wake_vorticity_abs_p99", r"Wake $|\omega_z|$ p99", fields),
    )
    fig, axes = plt.subplots(2, 3, figsize=(14.5, 8.5), constrained_layout=True)
    primary_x = np.asarray([summary[label]["Re_c"] for label in PRIMARY_LABELS], dtype=float)
    for ax, (key, ylabel, source) in zip(axes.flat, panels):
        primary_y = np.asarray([
            float(source[label][key]) if source.get(label, {}).get(key) is not None else math.nan
            for label in PRIMARY_LABELS
        ])
        finite = np.isfinite(primary_y)
        ax.plot(primary_x[finite], primary_y[finite], "o-", color="#264653", lw=1.4, ms=6, label="screening path")
        control = source.get("re1e4_f180", {}).get(key)
        if control is not None and math.isfinite(float(control)):
            ax.plot(1.0e4, float(control), marker="x", color="#d62828", ms=8, mew=2, ls="none", label="Re=1e4 f180")
        ax.set_xscale("log")
        ax.set_xlabel(r"$Re_c$")
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.25, which="both")
    axes[0, 0].legend(fontsize=8)
    fig.suptitle("MFC Reynolds-number screening metrics (mixed-grid controls labeled)", fontsize=15, fontweight="bold")
    fig.savefig(output / "reynolds_screening_metric_trends.png", dpi=240, facecolor="white")
    plt.close(fig)


def neighboring_reynolds_changes(
    summaries: list[dict[str, Any]], field_payload: dict[str, Any]
) -> list[dict[str, Any]]:
    summary = {row["label"]: row for row in summaries}
    fields = {row["label"]: row for row in field_payload.get("metrics", [])}
    rows: list[dict[str, Any]] = []
    keys = (
        ("CL_mean", summary),
        ("CL_rms", summary),
        ("CD_mean", summary),
        ("CD_rms", summary),
        ("shock_standoff_mean", summary),
        ("shock_angle_mean_deg", summary),
        ("centerline_reverse_flow_extent_over_c", fields),
        ("wake_vorticity_abs_p99", fields),
        ("wake_enstrophy_mean", fields),
    )
    for left, right in zip(PRIMARY_LABELS[:-1], PRIMARY_LABELS[1:]):
        row: dict[str, Any] = {
            "from_case": left,
            "to_case": right,
            "Re_ratio": float(summary[right]["Re_c"]) / float(summary[left]["Re_c"]),
            "grid_transition": f"{summary[left]['grid']}->{summary[right]['grid']}",
        }
        for key, source in keys:
            first = source.get(left, {}).get(key)
            second = source.get(right, {}).get(key)
            row[f"{key}_relative_change"] = (
                safe_relative(float(first), float(second))
                if first is not None and second is not None
                else None
            )
        rows.append(row)
    return rows


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def core_files(root: Path, summary_dir: Path, visuals_dir: Path) -> Iterable[Path]:
    for fixed in (root / "case_table.tsv", root / "long_view" / "long_view_manifest.json", root / "long_view" / "LONG_VIEW_OK.txt"):
        if fixed.is_file():
            yield fixed
    for directory in (root / "cases", summary_dir, visuals_dir):
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix.lower() != ".mp4" and "matplotlib-cache" not in path.parts:
                yield path
    ml_dir = root / "ml_dataset"
    for name in (
        "DATASET_OK.txt",
        "DATASET_CARD.md",
        "dataset_report.json",
        "normalization.json",
        "dataset_balance.csv",
        "manifest.jsonl",
        "splits.csv",
        "vortex_catalogue.csv",
        "shock_catalogue.csv",
        "cv_dataset_loader.py",
        "export.log",
    ):
        path = ml_dir / name
        if path.is_file():
            yield path
    for path in (ml_dir / "catalogues").glob("*_stage8_catalogue.csv"):
        if path.is_file():
            yield path
    for path in root.glob("*.out"):
        yield path
    for path in root.glob("*.err"):
        yield path
    for path in root.glob("*.sbatch"):
        yield path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-root", type=Path, required=True)
    parser.add_argument("--case-table", type=Path, required=True)
    args = parser.parse_args()
    root = args.analysis_root.resolve()
    cases = read_case_table(args.case_table.resolve())
    summary_dir = root / "summary"
    visuals_dir = root / "visuals"
    summary_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[dict[str, Any]] = []
    histories: dict[str, list[dict[str, str]]] = {}
    shocks: dict[str, list[dict[str, str]]] = {}
    spectra: dict[str, list[dict[str, str]]] = {}
    for case in cases:
        directory = root / "cases" / case.label
        metrics_path = directory / "mfc_hll_article_metrics.json"
        if not metrics_path.is_file():
            raise RuntimeError(f"missing analyzer output for {case.label}: {metrics_path}")
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        summaries.append(per_case_summary(case, payload))
        histories[case.label] = read_csv(directory / "mfc_hll_force_history.csv")
        shocks[case.label] = read_csv(directory / "mfc_hll_shock_history.csv")
        spectrum_path = directory / "mfc_hll_lift_spectrum.csv"
        spectra[case.label] = read_csv(spectrum_path) if spectrum_path.is_file() else []

    write_csv(summary_dir / "reynolds_force_shock_summary.csv", summaries)
    plot_histories(summary_dir, cases, histories)
    plot_force_components(summary_dir, summaries)
    plot_spectra(summary_dir, cases, spectra)
    plot_shock(summary_dir, cases, shocks)

    long_rows = histories["re1e6_long_t31"]
    windows, continuity = long_window_analysis(long_rows)
    write_csv(summary_dir / "hll_t31_five_unit_windows.csv", windows)
    write_csv(summary_dir / "hll_t31_restart_continuity.csv", continuity)
    plot_long(summary_dir, long_rows, windows)

    by_label = {row["label"]: row for row in summaries}
    coarse = by_label["re1e4_f180"]
    fine = by_label["re1e4_f270"]
    grid_keys = ("CL_mean", "CL_rms", "CD_mean", "CD_rms", "shock_standoff_mean", "shock_angle_mean_deg")
    grid_metrics = {
        key: safe_relative(float(coarse[key]), float(fine[key]))
        for key in grid_keys
        if coarse.get(key) is not None and fine.get(key) is not None
    }
    if len(grid_metrics) != len(grid_keys):
        grid_status = "GRID_SCREEN_INCOMPLETE"
    elif max(grid_metrics.values()) <= 0.05:
        grid_status = "SCREEN_PASS_LE_5_PERCENT"
    else:
        grid_status = "GRID_SENSITIVE"

    field_payload = json.loads((visuals_dir / "mfc_reynolds_field_metrics.json").read_text(encoding="utf-8"))
    ml_report_path = root / "ml_dataset" / "dataset_report.json"
    if not ml_report_path.is_file():
        raise RuntimeError("computer-vision dataset report is missing")
    ml_report = json.loads(ml_report_path.read_text(encoding="utf-8"))
    if ml_report.get("status") != "PASS" or int(ml_report.get("frames", 0)) < 300:
        raise RuntimeError("computer-vision dataset did not pass its output gates")
    plot_summary_metrics(summary_dir, summaries, field_payload)
    neighbor_changes = neighboring_reynolds_changes(summaries, field_payload)
    write_csv(summary_dir / "reynolds_neighbor_relative_changes.csv", neighbor_changes)
    window_keys = ("CL_mean", "CL_rms", "CD_mean", "CD_rms")
    stationarity = {
        f"{key}_relative_span": (
            max(float(row[key]) for row in windows) - min(float(row[key]) for row in windows)
        ) / max(max(abs(float(row[key])) for row in windows), 1.0e-12)
        for key in window_keys
    }
    long_manifest = json.loads((root / "long_view" / "long_view_manifest.json").read_text(encoding="utf-8"))
    boundary_rows = long_manifest.get("boundary_identity", [])
    boundary_audit_status = long_manifest.get("boundary_audit_status", "FAIL")
    accepted_boundary_statuses = {
        "BYTE_IDENTICAL_RAW",
        "NONIDENTICAL_RAW_PLUS_CHAIN_PROVENANCE",
        "SINGLE_RETAINED_RAW_PLUS_MARKER",
        "DERIVED_HISTORY_PLUS_MARKER",
    }
    boundary_audit_pass = (
        len(boundary_rows) == 5
        and boundary_audit_status == "PASS_HYBRID_RETAINED_AND_DERIVED"
        and all(
            row.get("right_restart_marker_valid") is True
            and row.get("stage_provenance", {}).get("valid") is True
            and row.get("audit_status") in accepted_boundary_statuses
            for row in boundary_rows
        )
    )
    if not boundary_audit_pass:
        raise RuntimeError("long-view restart-boundary audit is incomplete or invalid")
    exact_boundary_count = sum(
        row.get("audit_status") == "BYTE_IDENTICAL_RAW" for row in boundary_rows
    )
    single_raw_boundary_count = sum(
        row.get("audit_status") == "SINGLE_RETAINED_RAW_PLUS_MARKER"
        for row in boundary_rows
    )
    derived_boundary_count = sum(
        row.get("audit_status") == "DERIVED_HISTORY_PLUS_MARKER"
        for row in boundary_rows
    )
    nonidentical_boundary_count = sum(
        row.get("audit_status") == "NONIDENTICAL_RAW_PLUS_CHAIN_PROVENANCE"
        for row in boundary_rows
    )
    force_sources = sorted({str(row.get("force_source")) for row in summaries})
    report = {
        "status": "PIPELINE_PASS",
        "scientific_assessment": "SCREENING_REVIEW_REQUIRED",
        "scope": "screening diagnostics; intermediate-Re f180 cases are not grid-convergence claims",
        "cases": summaries,
        "field_metrics": field_payload,
        "computer_vision_dataset": {
            "path": str((root / "ml_dataset").resolve()),
            "schema_version": ml_report.get("schema_version"),
            "frames": ml_report.get("frames"),
            "shape_hw": ml_report.get("shape_hw"),
            "field_names": ml_report.get("field_names"),
            "split_counts": ml_report.get("split_counts"),
            "vortex_catalogue_rows": ml_report.get("vortex_catalogue_rows"),
            "shock_pass_frames": ml_report.get("shock_pass_frames"),
            "normalization": ml_report.get("normalization"),
            "label_qualification": ml_report.get("label_qualification"),
        },
        "neighboring_reynolds_relative_changes": neighbor_changes,
        "re1e4_f180_f270_relative_differences": grid_metrics,
        "re1e4_grid_screen_status": grid_status,
        "hll_t31_five_unit_windows": windows,
        "hll_t31_window_relative_spans": stationarity,
        "hll_restart_continuity": continuity,
        "hll_restart_boundary_audit_status": boundary_audit_status,
        "hll_restart_boundary_exact_count": exact_boundary_count,
        "hll_restart_boundary_nonidentical_count": nonidentical_boundary_count,
        "hll_restart_boundary_single_raw_count": single_raw_boundary_count,
        "hll_restart_boundary_derived_count": derived_boundary_count,
        "hll_restart_boundary_hash_status": (
            "RECORDED_NOT_ASSUMED_CONTINUOUS"
            if exact_boundary_count or nonidentical_boundary_count
            else "NO_DUPLICATED_RAW_BOUNDARY_RETAINED"
        ),
        "force_sources": force_sources,
        "interpretation_rules": {
            "short_spectra": "do not call a shedding peak resolved unless at least five cycles occur in the selected window",
            "forces": "field-reconstructed forces remain provisional until cross-checked against finite native MFC loads",
            "reynolds": "repeat only the first intermediate Reynolds number showing re-emergent fine structure on f270",
        },
    }
    (summary_dir / "MFC_REYNOLDS_T31_AUDIT.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )

    lines = [
        "# MFC Reynolds and HLL t=31 audit",
        "",
        "All cases are Mach 3, alpha=40 deg, viscous/no-model, unmapped-WENO5/HLL.",
        "The Re=5e4 and Re=1e5 f180 results are screening controls, not final grid-converged evidence.",
        "",
        "## Reynolds summary",
        "",
        "| Case | Grid | CL mean | CL RMS | CD mean | CD RMS | St | Shock d/c | Force source |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in summaries:
        if row["label"] == "re1e6_long_t31":
            continue
        fmt = lambda value: "NA" if value is None else f"{float(value):.5g}"
        lines.append(
            f"| {row['display']} | {row['grid']} | {fmt(row['CL_mean'])} | {fmt(row['CL_rms'])} | "
            f"{fmt(row['CD_mean'])} | {fmt(row['CD_rms'])} | {fmt(row['Strouhal'])} | "
            f"{fmt(row['shock_standoff_mean'])} | {row['force_source']} |"
        )
    lines.extend(
        [
            "",
            "## Gates",
            "",
            f"- Re=1e4 f180/f270 screen: `{grid_status}`.",
            f"- Restart-boundary audit through t=31: `{boundary_audit_status}` "
            f"({exact_boundary_count} byte-identical; "
            f"{nonidentical_boundary_count} nonidentical-with-provenance; "
            f"{single_raw_boundary_count} single-retained-raw; "
            f"{derived_boundary_count} derived-history-plus-marker).",
            f"- Force sources present: `{', '.join(force_sources)}`.",
            "- Short t=3..6 spectra are screening values unless their JSON status resolves at least five cycles.",
            "- The t=31 window table tests drift over five consecutive five-time-unit intervals.",
            f"- ML dataset: `{ml_report.get('frames')}` unique frames, schema "
            f"`{ml_report.get('schema_version')}`; labels are physics-derived weak labels.",
            "- Final physical interpretation must use the plots and numerical tables together.",
        ]
    )
    (summary_dir / "MFC_REYNOLDS_T31_SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    movie_list = sorted(visuals_dir.glob("*.mp4"))
    (summary_dir / "MOVIE_PATHS.txt").write_text(
        "".join(f"{path}\n" for path in movie_list), encoding="utf-8"
    )
    archive = root / "MFC_REYNOLDS_T31_ANALYSIS_CORE.zip"
    unique = sorted(set(core_files(root, summary_dir, visuals_dir)))
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for path in unique:
            if path == archive or path.name.endswith(".sha256.txt") and path.parent == root:
                continue
            target.write(path, path.relative_to(root))
    archive_sha = sha256(archive)
    (root / f"{archive.name}.sha256.txt").write_text(f"{archive_sha}  {archive.name}\n", encoding="utf-8")
    (root / "ANALYSIS_COMPLETE.txt").write_text(
        f"status=PASS\ncore_archive={archive}\nmovies={len(movie_list)}\n"
        f"ml_dataset={root / 'ml_dataset'}\nml_frames={ml_report.get('frames')}\n",
        encoding="utf-8",
    )
    print("MFC_REYNOLDS_T31_AGGREGATE=PASS")
    print(f"UPLOAD_CORE={archive}")
    for movie in movie_list:
        print(f"UPLOAD_MOVIE={movie}")
    print(f"TRAINING_DATASET={root / 'ml_dataset'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
