#!/usr/bin/env python3
"""Temporal statistics for the unsteady MFC diamond-airfoil Euler wake.

This companion to ``analyze_mfc.py`` deliberately does not demand pointwise
steady convergence in the Kelvin--Helmholtz wake.  It computes online mean/RMS
fields, shock-trace statistics, and (when ``ib_state_wrt`` is enabled) force
coefficients from MFC's formatted immersed-boundary load history.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Iterable

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mfc-matplotlib")

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from analyze_mfc import (
    MACH_INF,
    RHO_INF,
    _derived,
    _load_mfc_data,
    _plot_field,
    _region_mask,
    _synthetic_data,
    _windward_shock_trace,
)


FIELDS = ("pressure_ratio", "rho", "mach")
LOAD_DRIFT_THRESHOLD = 1.0e-2
SHOCK_ANGLE_STD_THRESHOLD_DEG = 1.0


def _available_steps(case_dir: Path) -> list[int]:
    rank_zero = case_dir / "silo_hdf5" / "p0"
    steps: list[int] = []
    for path in rank_zero.glob("*.silo"):
        try:
            steps.append(int(path.stem))
        except ValueError:
            continue
    return sorted(set(steps))


def _select_statistical_steps(steps: list[int], max_samples: int = 32) -> list[int]:
    positive = [step for step in steps if step > 0]
    if len(positive) < 4:
        return positive
    start = positive[len(positive) // 2]
    selected = [step for step in positive if step >= start]
    if len(selected) <= max_samples:
        return selected
    indices = np.linspace(0, len(selected) - 1, max_samples, dtype=int)
    return [selected[index] for index in np.unique(indices)]


def _online_field_statistics(
    items: Iterable[tuple[int, object]],
    alpha_deg: float,
    crop: tuple[float, ...],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, object], object, np.ndarray]:
    count = 0
    means: dict[str, np.ndarray] = {}
    m2: dict[str, np.ndarray] = {}
    trace_angles: list[float] = []
    trace_rows: list[np.ndarray] = []
    trace_x: np.ndarray | None = None
    last_data: object | None = None
    plot_mask: np.ndarray | None = None
    used_steps: list[int] = []

    for step, data in items:
        fields = _derived(data)
        x = np.asarray(data.x_cc)
        y = np.asarray(data.y_cc)
        region = _region_mask(x, y, crop)
        fluid_region = fields["fluid"] & region
        if plot_mask is None:
            plot_mask = fluid_region
        else:
            plot_mask &= fluid_region

        count += 1
        used_steps.append(step)
        for name in FIELDS:
            values = np.asarray(fields[name], dtype=float)
            if name not in means:
                means[name] = np.zeros_like(values)
                m2[name] = np.zeros_like(values)
            delta = values - means[name]
            means[name] += delta / count
            m2[name] += delta * (values - means[name])

        trace = _windward_shock_trace(data, fields, alpha_deg)
        accepted = np.asarray(trace["accepted"], dtype=bool)
        trace_y = np.where(accepted, np.asarray(trace["y"], dtype=float), np.nan)
        trace_rows.append(trace_y)
        trace_x = np.asarray(trace["x"], dtype=float)
        angle = float(trace["angle_to_freestream_deg"])
        if np.isfinite(angle):
            trace_angles.append(angle)
        last_data = data

    if count == 0 or last_data is None or plot_mask is None or trace_x is None:
        raise RuntimeError("No temporal snapshots were available for analysis")

    rms = {
        name: np.sqrt(np.maximum(m2[name] / max(count - 1, 1), 0.0))
        for name in FIELDS
    }
    trace_matrix = np.vstack(trace_rows)
    valid_counts = np.sum(np.isfinite(trace_matrix), axis=0)
    trace_sum = np.nansum(trace_matrix, axis=0)
    trace_mean = np.divide(
        trace_sum,
        valid_counts,
        out=np.full(trace_sum.shape, np.nan, dtype=float),
        where=valid_counts > 0,
    )
    centered = trace_matrix - trace_mean[None, :]
    centered[~np.isfinite(trace_matrix)] = np.nan
    trace_var_sum = np.nansum(centered**2, axis=0)
    trace_std = np.sqrt(
        np.divide(
            trace_var_sum,
            np.maximum(valid_counts - 1, 1),
            out=np.full(trace_var_sum.shape, np.nan, dtype=float),
            where=valid_counts > 1,
        )
    )
    finite_trace_std = trace_std[np.isfinite(trace_std)]
    angle_array = np.asarray(trace_angles, dtype=float)
    trace_metrics: dict[str, object] = {
        "sample_steps": used_steps,
        "sample_count": count,
        "shock_angle_to_freestream_mean_deg": (
            float(np.mean(angle_array)) if angle_array.size else None
        ),
        "shock_angle_to_freestream_std_deg": (
            float(np.std(angle_array, ddof=1)) if angle_array.size > 1 else None
        ),
        "mean_pointwise_shock_trace_std_over_c": (
            float(np.mean(finite_trace_std)) if finite_trace_std.size else None
        ),
        "max_pointwise_shock_trace_std_over_c": (
            float(np.max(finite_trace_std)) if finite_trace_std.size else None
        ),
        "trace_x": trace_x,
        "trace_mean_y": trace_mean,
        "trace_std_y": trace_std,
    }
    return means, rms, trace_metrics, last_data, plot_mask


def _save_mean_rms_figure(
    data: object,
    means: dict[str, np.ndarray],
    rms: dict[str, np.ndarray],
    mask: np.ndarray,
    alpha_deg: float,
    crop: tuple[float, ...],
    output: Path,
) -> None:
    x = np.asarray(data.x_cc)
    y = np.asarray(data.y_cc)
    fig, axes = plt.subplots(2, 3, figsize=(14.2, 7.8), constrained_layout=True)
    labels = {
        "pressure_ratio": r"$p/p_\infty$",
        "rho": r"$\rho/\rho_\infty$",
        "mach": r"$M$",
    }
    for col, name in enumerate(FIELDS):
        label = labels[name]
        _plot_field(
            axes[0, col],
            x,
            y,
            means[name],
            mask,
            crop,
            alpha_deg,
            f"Temporal mean: {label}",
            label,
            "turbo",
        )
        _plot_field(
            axes[1, col],
            x,
            y,
            rms[name],
            mask,
            crop,
            alpha_deg,
            f"Temporal RMS: {label}",
            f"RMS({label})",
            "magma",
        )
    fig.suptitle(
        f"MFC unsteady Euler statistics: Mach 3, alpha={alpha_deg:g} deg",
        fontsize=13,
        fontweight="bold",
    )
    fig.savefig(output, dpi=300, bbox_inches="tight")
    plt.close(fig)


def _save_trace_statistics(trace: dict[str, object], output: Path) -> None:
    x = np.asarray(trace["trace_x"], dtype=float)
    mean = np.asarray(trace["trace_mean_y"], dtype=float)
    std = np.asarray(trace["trace_std_y"], dtype=float)
    finite = np.isfinite(mean)
    fig, ax = plt.subplots(figsize=(8.0, 4.8), constrained_layout=True)
    ax.plot(x[finite], mean[finite], color="#d73027", lw=1.8, label="mean shock trace")
    ax.fill_between(
        x[finite],
        mean[finite] - std[finite],
        mean[finite] + std[finite],
        color="#fc8d59",
        alpha=0.35,
        label=r"$\pm1$ temporal standard deviation",
    )
    ax.set_xlabel(r"$x/c$")
    ax.set_ylabel(r"$y/c$")
    ax.set_title("Windward compression-front temporal statistics")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.savefig(output, dpi=300, bbox_inches="tight")
    plt.close(fig)


def _load_statistics(case_dir: Path, alpha_deg: float, sample_count: int) -> dict[str, object]:
    load_file = case_dir / "D" / "ib_1.txt"
    if not load_file.is_file():
        return {
            "status": "MISSING_IB_LOAD_HISTORY",
            "path": str(load_file),
            "sample_count": 0,
        }

    values = np.loadtxt(load_file, skiprows=1, ndmin=2)
    if values.shape[0] < 4 or values.shape[1] < 7:
        return {
            "status": "INSUFFICIENT_IB_LOAD_HISTORY",
            "path": str(load_file),
            "sample_count": int(values.shape[0]),
        }

    values = values[-min(sample_count, values.shape[0]) :]
    time = values[:, 0]
    fx = values[:, 1]
    fy = values[:, 2]
    tau_z = values[:, 6]
    alpha = math.radians(alpha_deg)
    q_ref = 0.5 * RHO_INF * MACH_INF**2
    cd = (fx * math.cos(alpha) + fy * math.sin(alpha)) / q_ref
    cl = (-fx * math.sin(alpha) + fy * math.cos(alpha)) / q_ref
    cm = tau_z / q_ref

    series = {"cd": cd, "cl": cl, "cm": cm}
    statistics: dict[str, object] = {}
    split = max(1, len(time) // 2)
    for name, array in series.items():
        first = float(np.mean(array[:split]))
        second = float(np.mean(array[split:]))
        mean = float(np.mean(array))
        rms = float(np.sqrt(np.mean((array - mean) ** 2)))
        drift = abs(second - first) / max(abs(mean), rms, 1.0e-12)
        statistics[name] = {
            "mean": mean,
            "rms_fluctuation": rms,
            "first_half_mean": first,
            "second_half_mean": second,
            "relative_half_window_drift": float(drift),
        }

    pass_drift = (
        len(time) >= 12
        and float(statistics["cd"]["relative_half_window_drift"]) <= LOAD_DRIFT_THRESHOLD
        and float(statistics["cl"]["relative_half_window_drift"]) <= LOAD_DRIFT_THRESHOLD
    )
    return {
        "status": "PASS_HALF_WINDOW_DRIFT" if pass_drift else "MORE_SAMPLES_OR_TIME_REQUIRED",
        "path": str(load_file),
        "sample_count": int(len(time)),
        "time": time,
        "series": series,
        "statistics": statistics,
        "load_drift_threshold": LOAD_DRIFT_THRESHOLD,
    }


def _save_load_history(loads: dict[str, object], output_dir: Path) -> None:
    if "time" not in loads:
        return
    time = np.asarray(loads["time"], dtype=float)
    series = loads["series"]
    columns = np.column_stack([time, series["cd"], series["cl"], series["cm"]])
    np.savetxt(
        output_dir / "mfc_load_history.csv",
        columns,
        delimiter=",",
        header="time,CD,CL,CM",
        comments="",
    )
    fig, axes = plt.subplots(3, 1, figsize=(8.0, 7.5), sharex=True, constrained_layout=True)
    for ax, name, label, color in (
        (axes[0], "cd", r"$C_D$", "#d73027"),
        (axes[1], "cl", r"$C_L$", "#4575b4"),
        (axes[2], "cm", r"$C_M$", "#1a9850"),
    ):
        values = np.asarray(series[name], dtype=float)
        ax.plot(time, values, color=color, lw=1.5)
        ax.axhline(float(np.mean(values)), color="black", ls="--", lw=1.0)
        ax.set_ylabel(label)
        ax.grid(alpha=0.25)
    axes[-1].set_xlabel("MFC nondimensional time")
    fig.suptitle("Immersed-boundary load history over statistical window", fontweight="bold")
    fig.savefig(output_dir / "mfc_load_history.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def _serializable_trace_metrics(trace: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in trace.items()
        if key not in {"trace_x", "trace_mean_y", "trace_std_y"}
    }


def _run(
    items: Iterable[tuple[int, object]],
    case_dir: Path | None,
    alpha_deg: float,
    crop: tuple[float, ...],
    output_dir: Path,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    means, rms, trace, last_data, mask = _online_field_statistics(items, alpha_deg, crop)
    _save_mean_rms_figure(
        last_data,
        means,
        rms,
        mask,
        alpha_deg,
        crop,
        output_dir / "mfc_mean_rms_fields.png",
    )
    _save_trace_statistics(trace, output_dir / "mfc_shock_trace_statistics.png")
    trace_columns = np.column_stack(
        [trace["trace_x"], trace["trace_mean_y"], trace["trace_std_y"]]
    )
    np.savetxt(
        output_dir / "mfc_shock_trace_statistics.csv",
        trace_columns,
        delimiter=",",
        header="x_over_c,mean_y_over_c,std_y_over_c",
        comments="",
    )

    loads = (
        _load_statistics(case_dir, alpha_deg, int(trace["sample_count"]))
        if case_dir is not None
        else {"status": "SELF_TEST_NO_LOAD_HISTORY", "sample_count": 0}
    )
    _save_load_history(loads, output_dir)
    clean_loads = {key: value for key, value in loads.items() if key not in {"time", "series"}}
    angle_std = trace["shock_angle_to_freestream_std_deg"]
    shock_stable = angle_std is not None and float(angle_std) <= SHOCK_ANGLE_STD_THRESHOLD_DEG
    temporal_assessment = (
        "PASS_PRELIMINARY_TEMPORAL"
        if shock_stable and loads["status"] == "PASS_HALF_WINDOW_DRIFT"
        else "MORE_TIME_OR_SAMPLES_REQUIRED"
    )
    metrics: dict[str, object] = {
        "solver_interpretation": "UNSTEADY_EULER_WAKE_WITH_NUMERICAL_REGULARIZATION",
        "alpha_deg": alpha_deg,
        "temporal_assessment": temporal_assessment,
        "shock_angle_std_threshold_deg": SHOCK_ANGLE_STD_THRESHOLD_DEG,
        "shock_trace_statistics": _serializable_trace_metrics(trace),
        "ib_load_statistics": clean_loads,
        "publication_readiness": "PENDING_COARSE_MEDIUM_AND_FAR_BOUNDARY_COMPARISONS",
    }
    (output_dir / "mfc_unsteady_metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = [
        "MFC UNSTEADY EULER VALIDATION SUMMARY",
        "======================================",
        f"Statistical samples: {trace['sample_count']}",
        f"Sample steps: {trace['sample_steps']}",
        "Shock angle mean/std: "
        f"{trace['shock_angle_to_freestream_mean_deg']} / "
        f"{trace['shock_angle_to_freestream_std_deg']} deg",
        f"Mean shock-trace pointwise std: {trace['mean_pointwise_shock_trace_std_over_c']}",
        f"IB load assessment: {loads['status']}",
        f"Temporal assessment: {temporal_assessment}",
        "Publication readiness: PENDING coarse/medium and far-boundary checks.",
        "Interpret individual Euler wake vortices qualitatively; their wavelength is grid-regularized.",
    ]
    (output_dir / "mfc_unsteady_summary.txt").write_text(
        "\n".join(summary) + "\n",
        encoding="utf-8",
    )
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", nargs="?", type=Path)
    parser.add_argument("--alpha", type=float, default=30.0)
    parser.add_argument("--steps", type=str, default=None, help="comma-separated saved steps")
    parser.add_argument("--max-samples", type=int, default=32)
    parser.add_argument("--crop", nargs=4, type=float, default=(-1.25, 2.75, -1.50, 1.75))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    crop = tuple(args.crop)

    if args.self_test:
        output = args.output or Path("mfc_unsteady_self_test")
        shifts = 0.006 * np.sin(np.linspace(0.0, 4.0 * np.pi, 14))
        items = (
            (index * 100, _synthetic_data(float(shift)))
            for index, shift in enumerate(shifts, start=1)
        )
        metrics = _run(items, None, args.alpha, crop, output)
    else:
        if args.case_dir is None:
            parser.error("case_dir is required unless --self-test is used")
        case_dir = args.case_dir.resolve()
        available = _available_steps(case_dir)
        if args.steps:
            steps = [int(value) for value in args.steps.split(",") if value.strip()]
        else:
            steps = _select_statistical_steps(available, max_samples=args.max_samples)
        missing = sorted(set(steps) - set(available))
        if missing:
            parser.error(f"requested steps are missing from the archive: {missing}")
        if len(steps) < 4:
            parser.error("at least four saved steps are required for unsteady statistics")
        output = args.output or case_dir / "analysis"
        items = ((step, _load_mfc_data(case_dir, step)) for step in steps)
        metrics = _run(items, case_dir, args.alpha, crop, output)

    print(json.dumps(metrics, indent=2, sort_keys=True))
    print(f"Unsteady analysis written to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
