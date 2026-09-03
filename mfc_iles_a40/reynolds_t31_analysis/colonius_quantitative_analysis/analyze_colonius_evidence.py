#!/usr/bin/env python3
"""Build quantitative, figure-only evidence for Tim Colonius.

This is a post-processor for the completed ``mfc-cv-physics-v1`` export.  It
does not run MFC and it deliberately does not infer lift or drag from the
512x512 training tensors.  The three diagnostics mirror the questions raised
by Tim Colonius:

1. Does the same-grid Reynolds ladder show a systematic change after startup?
2. Which structures survive the f180 -> f270 resolution change at Re=1e4?
3. Do the retained Re=1e6 fields remain statistically similar over the final
   continuous segment, rather than merely looking similar at three times?

The output is descriptive evidence, not a grid-convergence or stationarity
certificate.  Shock and vortex quantities inherited from the CV export remain
physics-derived weak labels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages


REQUIRED_CASES = (
    "re1e4_f180",
    "re1e4_f270",
    "re5e4_f180",
    "re1e5_f180",
    "re1e6_retained",
)
COMMON_GRID_CASES = ("re1e4_f180", "re5e4_f180", "re1e5_f180")
COLORS = {
    "re1e4_f180": "#264653",
    "re1e4_f270": "#8f2d56",
    "re5e4_f180": "#2a9d8f",
    "re1e5_f180": "#e9c46a",
    "re1e6_retained": "#e76f51",
}
DISPLAY = {
    "re1e4_f180": r"$Re_c=10^4$, f180",
    "re1e4_f270": r"$Re_c=10^4$, f270",
    "re5e4_f180": r"$Re_c=5\times10^4$, f180",
    "re1e5_f180": r"$Re_c=10^5$, f180",
    "re1e6_retained": r"$Re_c=10^6$, f270",
}
SCALAR_METRICS = (
    "wake_enstrophy",
    "wake_abs_vorticity_p99",
    "wake_pressure_rms",
    "near_body_pressure_high_k_fraction",
    "shock_standoff_over_c",
    "pressure_change_rate",
    "vorticity_change_rate",
)
SUMMARY_METRICS = (
    "wake_enstrophy",
    "wake_abs_vorticity_p99",
    "wake_pressure_rms",
    "near_body_pressure_high_k_fraction",
    "shock_standoff_over_c",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def read_manifest(root: Path, allow_small: bool = False) -> list[dict[str, Any]]:
    marker = root / "DATASET_OK.txt"
    if not marker.is_file() or "status=PASS" not in marker.read_text(encoding="utf-8"):
        raise RuntimeError(f"completed-dataset marker is absent or not PASS: {marker}")
    path = root / "manifest.jsonl"
    if not path.is_file():
        raise RuntimeError(f"manifest is absent: {path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not allow_small and len(rows) < 300:
        raise RuntimeError(f"only {len(rows)} manifest rows were found; expected the full export")
    cases = {str(row["case"]) for row in rows}
    missing = sorted(set(REQUIRED_CASES) - cases)
    if missing:
        raise RuntimeError(f"dataset lacks required cases: {missing}")
    for row in rows:
        tensor = root / str(row["tensor"])
        if not tensor.is_file() or tensor.stat().st_size == 0:
            raise RuntimeError(
                f"full tensor is missing: {tensor}. The LITE archive is insufficient; "
                "run this on Unity's complete ml_dataset directory."
            )
    return rows


def read_shock_catalogue(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "shock_catalogue.csv"
    if not path.is_file():
        return {}
    result: dict[str, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            result[str(row["dataset_id"])] = row
    return result


def decode_names(values: np.ndarray) -> list[str]:
    result = []
    for item in values.tolist():
        if isinstance(item, bytes):
            result.append(item.decode("utf-8"))
        else:
            result.append(str(item))
    return result


def load_tensor(root: Path, row: dict[str, Any]) -> dict[str, Any]:
    path = root / str(row["tensor"])
    with np.load(path, allow_pickle=False) as archive:
        required = {"fields", "field_names", "x", "y", "fluid_mask", "shock_mask", "shock_ridge"}
        missing = sorted(required - set(archive.files))
        if missing:
            raise RuntimeError(f"{path} lacks tensor arrays: {missing}")
        names = decode_names(np.asarray(archive["field_names"]))
        fields = np.asarray(archive["fields"], dtype=np.float32)
        if fields.ndim != 3 or fields.shape[0] != len(names):
            raise RuntimeError(f"invalid fields shape in {path}: {fields.shape}")
        mapping = {name: fields[index] for index, name in enumerate(names)}
        needed = {"pressure", "omega_z", "u", "v"}
        absent = sorted(needed - set(mapping))
        if absent:
            raise RuntimeError(f"{path} lacks physical channels: {absent}")
        x = np.asarray(archive["x"], dtype=np.float64)
        y = np.asarray(archive["y"], dtype=np.float64)
        fluid = np.asarray(archive["fluid_mask"], dtype=bool)
        shock = np.asarray(archive["shock_mask"], dtype=bool)
        ridge = np.asarray(archive["shock_ridge"], dtype=bool)
    expected = (len(y), len(x))
    if fields.shape[1:] != expected or fluid.shape != expected:
        raise RuntimeError(f"tensor/grid orientation mismatch in {path}")
    for name in needed:
        if not np.isfinite(mapping[name][fluid]).all():
            raise RuntimeError(f"non-finite {name} values in {path}")
    return {
        "x": x,
        "y": y,
        "fluid": fluid,
        "shock": shock,
        "ridge": ridge,
        **mapping,
    }


def fft_gaussian(values: np.ndarray, dx: float, dy: float, sigma: float) -> np.ndarray:
    """Gaussian low-pass filter with reflection padding and no SciPy dependency."""

    if sigma <= 0.0:
        return np.asarray(values, dtype=np.float64)
    pad_x = max(4, int(math.ceil(4.0 * sigma / dx)))
    pad_y = max(4, int(math.ceil(4.0 * sigma / dy)))
    padded = np.pad(np.asarray(values, dtype=np.float64), ((pad_y, pad_y), (pad_x, pad_x)), mode="reflect")
    fy = np.fft.fftfreq(padded.shape[0], d=dy)[:, None]
    fx = np.fft.rfftfreq(padded.shape[1], d=dx)[None, :]
    response = np.exp(-2.0 * math.pi**2 * sigma**2 * (fx * fx + fy * fy))
    smooth = np.fft.irfft2(np.fft.rfft2(padded) * response, s=padded.shape)
    return smooth[pad_y:-pad_y, pad_x:-pad_x]


def masked_lowpass(
    field: np.ndarray,
    mask: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    sigma: float,
) -> tuple[np.ndarray, np.ndarray]:
    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    weight = fft_gaussian(mask.astype(np.float64), dx, dy, sigma)
    numerator = fft_gaussian(np.where(mask, field, 0.0), dx, dy, sigma)
    output = numerator / np.maximum(weight, 1.0e-8)
    valid = mask & (weight >= 0.55)
    return output, valid


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return np.asarray(mask, dtype=bool)
    source = np.pad(np.asarray(mask, dtype=np.int32), radius, mode="constant")
    integral = np.pad(source, ((1, 0), (1, 0)), mode="constant").cumsum(0).cumsum(1)
    width = 2 * radius + 1
    height, columns = mask.shape
    sums = (
        integral[width : width + height, width : width + columns]
        - integral[:height, width : width + columns]
        - integral[width : width + height, :columns]
        + integral[:height, :columns]
    )
    return sums > 0


def masks(fields: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    xx, yy = np.meshgrid(fields["x"], fields["y"], indexing="xy")
    alpha = math.radians(40.0)
    streamwise = (xx - 1.0) * math.cos(alpha) + yy * math.sin(alpha)
    normal = -(xx - 1.0) * math.sin(alpha) + yy * math.cos(alpha)
    wake = (
        fields["fluid"]
        & (streamwise >= 0.08)
        & (streamwise <= 3.0)
        & (np.abs(normal) <= 0.75)
    )
    near_body = (
        fields["fluid"]
        & (xx >= -0.45)
        & (xx <= 1.45)
        & (yy >= -0.55)
        & (yy <= 0.55)
        & ~dilate(fields["shock"], 4)
    )
    if np.count_nonzero(wake) < 100 or np.count_nonzero(near_body) < 100:
        raise RuntimeError("analysis masks contain too few valid pixels")
    return wake, near_body


def centered_rms(field: np.ndarray, mask: np.ndarray) -> float:
    selected = np.asarray(field[mask], dtype=np.float64)
    return float(np.sqrt(np.mean((selected - np.mean(selected)) ** 2)))


def relative_change_rate(
    previous: np.ndarray,
    current: np.ndarray,
    mask: np.ndarray,
    delta_time: float,
) -> float:
    first = np.asarray(previous[mask], dtype=np.float64)
    second = np.asarray(current[mask], dtype=np.float64)
    first -= np.mean(first)
    second -= np.mean(second)
    scale = 0.5 * (
        float(np.sqrt(np.mean(first * first)))
        + float(np.sqrt(np.mean(second * second)))
    )
    change = float(np.sqrt(np.mean((second - first) ** 2)))
    return change / max(scale * delta_time, 1.0e-12)


def shock_standoff_from_ridge(fields: dict[str, Any]) -> float | None:
    yy, xx = np.nonzero(fields["ridge"])
    if len(xx) < 8:
        return None
    xp = fields["x"][xx]
    yp = fields["y"][yy]
    alpha = math.radians(40.0)
    s = xp * math.cos(alpha) + yp * math.sin(alpha)
    n = -xp * math.sin(alpha) + yp * math.cos(alpha)
    accepted = (np.abs(n) <= 0.24) & (s < 0.0)
    if np.count_nonzero(accepted) < 8:
        return None
    intercept, _ = np.polyfit(n[accepted], s[accepted], 1)[::-1]
    return float(-intercept)


def frame_metrics(
    fields: dict[str, Any],
    shock_row: dict[str, Any] | None,
) -> tuple[dict[str, float | None], np.ndarray, np.ndarray]:
    wake, near_body = masks(fields)
    omega = fields["omega_z"]
    pressure = fields["pressure"]
    pressure_low, pressure_low_valid = masked_lowpass(
        pressure, near_body, fields["x"], fields["y"], sigma=0.05
    )
    high_valid = near_body & pressure_low_valid
    high_rms = float(np.sqrt(np.mean((pressure[high_valid] - pressure_low[high_valid]) ** 2)))
    total_rms = centered_rms(pressure, high_valid)
    stand = None
    if shock_row is not None and str(shock_row.get("status", "")) == "PASS":
        stand = finite(shock_row.get("stand_off_over_c"))
    if stand is None:
        stand = shock_standoff_from_ridge(fields)
    selected_omega = np.abs(np.asarray(omega[wake], dtype=np.float64))
    result: dict[str, float | None] = {
        "wake_enstrophy": float(np.mean(selected_omega**2)),
        "wake_abs_vorticity_p99": float(np.percentile(selected_omega, 99.0)),
        "wake_pressure_rms": centered_rms(pressure, wake),
        "near_body_pressure_high_k_fraction": high_rms / max(total_rms, 1.0e-12),
        "shock_standoff_over_c": stand,
        "pressure_change_rate": None,
        "vorticity_change_rate": None,
    }
    return result, wake, near_body


def nominal_spacing(rows: list[dict[str, Any]]) -> float:
    times = np.asarray(sorted({float(row["time"]) for row in rows}), dtype=float)
    gaps = np.diff(times)
    positive = gaps[gaps > 1.0e-10]
    if not positive.size:
        raise RuntimeError("case has fewer than two distinct times")
    return float(np.min(positive))


def analyze_cases(
    root: Path,
    manifest: list[dict[str, Any]],
    shock: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in manifest:
        grouped[str(row["case"])].append(row)
    output: list[dict[str, Any]] = []
    total = sum(len(grouped[case]) for case in REQUIRED_CASES)
    counter = 0
    for case in REQUIRED_CASES:
        rows = sorted(grouped[case], key=lambda item: float(item["time"]))
        cadence = nominal_spacing(rows)
        previous_fields: dict[str, Any] | None = None
        previous_time: float | None = None
        for row in rows:
            counter += 1
            fields = load_tensor(root, row)
            metrics, wake, _ = frame_metrics(fields, shock.get(str(row["dataset_id"])))
            current_time = float(row["time"])
            if previous_fields is not None and previous_time is not None:
                gap = current_time - previous_time
                if gap <= 1.51 * cadence:
                    metrics["pressure_change_rate"] = relative_change_rate(
                        previous_fields["pressure"], fields["pressure"], wake, gap
                    )
                    metrics["vorticity_change_rate"] = relative_change_rate(
                        previous_fields["omega_z"], fields["omega_z"], wake, gap
                    )
            output.append(
                {
                    "dataset_id": row["dataset_id"],
                    "case": case,
                    "Re_c": float(row["Re_c"]),
                    "grid": row["grid"],
                    "time": current_time,
                    "source_step": int(row["source_step"]),
                    "continuous_cadence": cadence,
                    **metrics,
                }
            )
            previous_fields = fields
            previous_time = current_time
            print(f"TIM_METRIC {counter}/{total} case={case} t={current_time:g}", flush=True)
    return output


def correlation(first: np.ndarray, second: np.ndarray, mask: np.ndarray) -> float:
    a = np.asarray(first[mask], dtype=np.float64)
    b = np.asarray(second[mask], dtype=np.float64)
    a -= np.mean(a)
    b -= np.mean(b)
    denominator = math.sqrt(float(np.dot(a, a) * np.dot(b, b)))
    return float(np.dot(a, b) / max(denominator, 1.0e-30))


def energy_ratio(first: np.ndarray, second: np.ndarray, mask: np.ndarray) -> float:
    first_energy = float(np.mean(np.asarray(first[mask], dtype=np.float64) ** 2))
    second_energy = float(np.mean(np.asarray(second[mask], dtype=np.float64) ** 2))
    return second_energy / max(first_energy, 1.0e-30)


def scale_separation(
    root: Path,
    manifest: list[dict[str, Any]],
    start_time: float = 3.0,
) -> list[dict[str, Any]]:
    by_case_time: dict[str, dict[float, dict[str, Any]]] = defaultdict(dict)
    for row in manifest:
        by_case_time[str(row["case"])][round(float(row["time"]), 9)] = row
    common = sorted(
        set(by_case_time["re1e4_f180"]) & set(by_case_time["re1e4_f270"])
    )
    common = [time for time in common if time >= start_time - 1.0e-9]
    if len(common) < 8:
        raise RuntimeError("too few matched f180/f270 Re=1e4 times for grid diagnostic")
    output: list[dict[str, Any]] = []
    for index, time in enumerate(common, start=1):
        coarse = load_tensor(root, by_case_time["re1e4_f180"][time])
        fine = load_tensor(root, by_case_time["re1e4_f270"][time])
        if not np.allclose(coarse["x"], fine["x"]) or not np.allclose(coarse["y"], fine["y"]):
            raise RuntimeError("training tensors do not share the declared fixed grid")
        coarse_wake, coarse_near = masks(coarse)
        fine_wake, fine_near = masks(fine)
        wake = coarse_wake & fine_wake
        near = coarse_near & fine_near
        record: dict[str, Any] = {"time": time}
        for field_name, region, label in (
            ("pressure", wake, "wake_pressure"),
            ("omega_z", wake, "wake_vorticity"),
            ("pressure", near, "near_body_pressure"),
        ):
            coarse_low, coarse_valid = masked_lowpass(
                coarse[field_name], region, coarse["x"], coarse["y"], sigma=0.10
            )
            fine_low, fine_valid = masked_lowpass(
                fine[field_name], region, fine["x"], fine["y"], sigma=0.10
            )
            valid = region & coarse_valid & fine_valid
            coarse_high = coarse[field_name] - coarse_low
            fine_high = fine[field_name] - fine_low
            record[f"{label}_large_scale_correlation"] = correlation(
                coarse_low, fine_low, valid
            )
            record[f"{label}_fine_over_coarse_small_scale_energy"] = energy_ratio(
                coarse_high, fine_high, valid
            )
        output.append(record)
        print(f"TIM_GRID {index}/{len(common)} t={time:g}", flush=True)
    return output


def continuous_segments(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: float(row["time"]))
    cadence = nominal_spacing(ordered)
    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous: float | None = None
    for row in ordered:
        time = float(row["time"])
        if previous is not None and time - previous > 1.51 * cadence:
            result.append(current)
            current = []
        current.append(row)
        previous = time
    if current:
        result.append(current)
    return result


def metric_summary(
    rows: list[dict[str, Any]],
    metrics: Iterable[str],
    window_name: str,
) -> list[dict[str, Any]]:
    if len(rows) < 4:
        raise RuntimeError(f"{window_name} has too few rows")
    time = np.asarray([float(row["time"]) for row in rows], dtype=float)
    duration = float(time[-1] - time[0])
    output: list[dict[str, Any]] = []
    for metric in metrics:
        pairs = [
            (float(row["time"]), finite(row.get(metric)))
            for row in rows
        ]
        pairs = [(t, value) for t, value in pairs if value is not None]
        if len(pairs) < 4:
            continue
        selected_time = np.asarray([item[0] for item in pairs], dtype=float)
        values = np.asarray([float(item[1]) for item in pairs], dtype=float)
        centered = selected_time - float(np.mean(selected_time))
        slope = float(np.polyfit(centered, values, 1)[0])
        mean = float(np.mean(values))
        output.append(
            {
                "window": window_name,
                "case": rows[0]["case"],
                "Re_c": rows[0]["Re_c"],
                "grid": rows[0]["grid"],
                "time_start": float(selected_time[0]),
                "time_end": float(selected_time[-1]),
                "samples": len(values),
                "metric": metric,
                "mean": mean,
                "temporal_std": float(np.std(values, ddof=1)),
                "slope_per_time": slope,
                "fractional_linear_drift": slope * max(duration, 0.0) / max(abs(mean), 1.0e-12),
            }
        )
    return output


def build_summaries(metrics: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in metrics:
        grouped[str(row["case"])].append(row)
    reynolds_summary: list[dict[str, Any]] = []
    for case in COMMON_GRID_CASES:
        selected = [row for row in grouped[case] if float(row["time"]) >= 3.0 - 1.0e-9]
        reynolds_summary.extend(metric_summary(selected, SUMMARY_METRICS, "post_startup_t3_t6"))
    high_segments = continuous_segments(grouped["re1e6_retained"])
    final_segment = max(high_segments, key=lambda segment: (float(segment[-1]["time"]) - float(segment[0]["time"]), len(segment)))
    if float(final_segment[-1]["time"]) < 31.0 - 1.0e-6:
        raise RuntimeError("the final Re=1e6 continuous segment does not reach t=31")
    stationarity = metric_summary(final_segment, SCALAR_METRICS, "re1e6_final_continuous_segment")
    return reynolds_summary, stationarity, final_segment


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"refusing to write empty CSV: {path}")
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(
            {
                key: "" if value is None else value
                for key, value in row.items()
            }
            for row in rows
        )


def values_for(rows: list[dict[str, Any]], metric: str) -> tuple[np.ndarray, np.ndarray]:
    pairs = [(finite(row.get("time")), finite(row.get(metric))) for row in rows]
    pairs = [(time, value) for time, value in pairs if time is not None and value is not None]
    return (
        np.asarray([item[0] for item in pairs], dtype=float),
        np.asarray([item[1] for item in pairs], dtype=float),
    )


def plot_reynolds_page(pdf: PdfPages, metrics: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in metrics:
        grouped[str(row["case"])].append(row)
    fig, axes = plt.subplots(3, 1, figsize=(8.5, 10.8), sharex=True)
    panels = (
        ("wake_enstrophy", r"Wake mean $\omega_z^2$", True),
        ("shock_standoff_over_c", r"Bow-shock stand-off $d_s/c$", False),
        ("near_body_pressure_high_k_fraction", "Near-body high-wavenumber pressure fraction", False),
    )
    for ax, (metric, ylabel, logarithmic) in zip(axes, panels):
        for case in COMMON_GRID_CASES:
            time, values = values_for(grouped[case], metric)
            ax.plot(time, values, lw=1.45, color=COLORS[case], label=DISPLAY[case])
        if logarithmic and all(np.all(values_for(grouped[case], metric)[1] > 0.0) for case in COMMON_GRID_CASES):
            ax.set_yscale("log")
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.25)
        ax.axvspan(0.0, 3.0, color="0.85", alpha=0.55)
    axes[0].legend(ncol=3, fontsize=8, loc="best")
    axes[-1].set_xlabel(r"MFC nondimensional time $t$")
    fig.suptitle(
        r"Same-grid Reynolds ladder: $M_\infty=3$, $\alpha=40^\circ$, f180",
        y=0.975,
        fontsize=14,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0.02, 0.075, 0.98, 0.94))
    fig.text(
        0.5,
        0.025,
        "Gray region is treated as startup; curves are descriptive and do not establish grid convergence.",
        ha="center",
        fontsize=8,
    )
    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def plot_grid_page(pdf: PdfPages, rows: list[dict[str, Any]]) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(8.5, 9.2), sharex=True)
    panels = (
        ("wake_pressure_large_scale_correlation", "Large-scale wake-pressure correlation", (-1.02, 1.02)),
        ("wake_vorticity_large_scale_correlation", "Large-scale wake-vorticity correlation", (-1.02, 1.02)),
        ("near_body_pressure_fine_over_coarse_small_scale_energy", "Near-body pressure: f270/f180 small-scale energy", None),
        ("wake_vorticity_fine_over_coarse_small_scale_energy", "Wake vorticity: f270/f180 small-scale energy", None),
    )
    for ax, (metric, ylabel, limits) in zip(axes.flat, panels):
        time, values = values_for(rows, metric)
        ax.plot(time, values, color="#5e3c99", lw=1.5)
        if "energy" in metric:
            ax.axhline(1.0, color="0.35", lw=0.8, ls="--")
        if limits is not None:
            ax.set_ylim(*limits)
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.25)
        ax.set_xlabel(r"MFC nondimensional time $t$")
    fig.suptitle(
        r"Resolution diagnostic at $Re_c=10^4$: f180 versus f270, $t\geq3$",
        y=0.975,
        fontsize=14,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0.02, 0.085, 0.98, 0.93))
    fig.text(
        0.5,
        0.025,
        r"Large scales use a Gaussian physical filter $\sigma=0.10c$; ratios above 1 indicate more small-scale energy on f270.",
        ha="center",
        fontsize=8,
    )
    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def plot_stationarity_page(pdf: PdfPages, rows: list[dict[str, Any]]) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(8.5, 9.2), sharex=True)
    panels = (
        ("wake_enstrophy", r"Wake mean $\omega_z^2$"),
        ("shock_standoff_over_c", r"Bow-shock stand-off $d_s/c$"),
        ("near_body_pressure_high_k_fraction", "Near-body high-wavenumber pressure fraction"),
        ("vorticity_change_rate", r"Wake vorticity relative change rate"),
    )
    for ax, (metric, ylabel) in zip(axes.flat, panels):
        time, values = values_for(rows, metric)
        ax.plot(time, values, color=COLORS["re1e6_retained"], lw=1.25)
        if len(time) >= 4:
            centered = time - np.mean(time)
            fit = np.polyval(np.polyfit(centered, values, 1), centered)
            ax.plot(time, fit, color="black", lw=1.0, ls="--", label="linear trend")
        ax.set_ylabel(ylabel)
        ax.set_xlabel(r"MFC nondimensional time $t$")
        ax.grid(alpha=0.25)
    axes[0, 0].legend(fontsize=8)
    fig.suptitle(
        r"Final continuous segment at $Re_c=10^6$, f270",
        y=0.975,
        fontsize=14,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0.02, 0.085, 0.98, 0.93))
    fig.text(
        0.5,
        0.025,
        "This tests late-time drift on the retained dense segment; it does not bridge pruned-data gaps.",
        ha="center",
        fontsize=8,
    )
    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def compact_mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [finite(row.get(key)) for row in rows]
    selected = [float(value) for value in values if value is not None]
    return float(np.mean(selected)) if selected else None


def write_assessment(
    path: Path,
    root: Path,
    manifest: list[dict[str, Any]],
    reynolds_summary: list[dict[str, Any]],
    grid_rows: list[dict[str, Any]],
    stationarity: list[dict[str, Any]],
    final_segment: list[dict[str, Any]],
) -> None:
    grid_means = {
        key: compact_mean(grid_rows, key)
        for key in grid_rows[0]
        if key != "time"
    }
    stationarity_by_metric = {str(row["metric"]): row for row in stationarity}
    lines = [
        "# Tim Colonius quantitative evidence — interpretation note",
        "",
        f"Input: `{root}` ({len(manifest)} completed tensor frames).",
        "",
        "## What this analysis answers",
        "",
        "1. The Reynolds comparison uses only the common f180 grid for Re=1e4, 5e4, and 1e5 over t=3..6.",
        "2. The Re=1e4 f180/f270 comparison separates large-scale correlation from small-scale energy sensitivity.",
        f"3. The Re=1e6 late-time check uses only the final continuous retained segment t={float(final_segment[0]['time']):g}..{float(final_segment[-1]['time']):g}.",
        "",
        "## Compact numerical readout",
        "",
        "Grid-control means over matched t>=3:",
    ]
    for key, value in grid_means.items():
        if value is not None:
            lines.append(f"- `{key}` = {value:.6g}")
    lines.extend(["", "Re=1e6 fractional linear drift over the final continuous segment:"])
    for metric in SUMMARY_METRICS:
        row = stationarity_by_metric.get(metric)
        if row is not None:
            lines.append(f"- `{metric}` = {float(row['fractional_linear_drift']):+.3%}")
    lines.extend(
        [
            "",
            "## Limits that must remain in the caption/email",
            "",
            "- These are descriptive post-processing diagnostics, not proof of grid convergence or statistical stationarity.",
            "- Instantaneous f180/f270 correlation is phase-sensitive; a low value alone does not prove that the large-scale statistics differ.",
            "- High-wavenumber pressure content is a resolution/noise diagnostic; it is not automatically numerical noise.",
            "- Shock locations inherit an algorithmic density-gradient ridge fit and should be visually spot-checked.",
            "- The Re=1e6 retained sequence contains gaps; no trend line is fitted across a gap.",
            "- Do not infer CL/CD from the 512x512 training tensors. Use native/raw immersed-boundary force analysis and a separate force-source cross-check.",
            "",
            "## What to send",
            "",
            "Send `TIM_COLONIUS_QUANTITATIVE_CHECKS.pdf` together with the previously prepared three-page field-comparison PDF. Do not send the ML tensors or catalogues unless Tim explicitly asks.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def analyze(root: Path, output: Path, allow_small: bool = False) -> dict[str, Any]:
    root = root.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest(root, allow_small=allow_small)
    shock = read_shock_catalogue(root)
    metrics = analyze_cases(root, manifest, shock)
    grid_rows = scale_separation(root, manifest)
    reynolds_summary, stationarity, final_segment = build_summaries(metrics)

    metrics_path = output / "tim_colonius_time_metrics.csv"
    grid_path = output / "tim_colonius_grid_scale_separation.csv"
    reynolds_path = output / "tim_colonius_same_grid_reynolds_summary.csv"
    stationarity_path = output / "tim_colonius_re1e6_stationarity_summary.csv"
    write_csv(metrics_path, metrics)
    write_csv(grid_path, grid_rows)
    write_csv(reynolds_path, reynolds_summary)
    write_csv(stationarity_path, stationarity)

    pdf_path = output / "TIM_COLONIUS_QUANTITATIVE_CHECKS.pdf"
    with PdfPages(pdf_path) as pdf:
        plot_reynolds_page(pdf, metrics)
        plot_grid_page(pdf, grid_rows)
        plot_stationarity_page(pdf, final_segment)
        metadata = pdf.infodict()
        metadata["Title"] = "MFC Mach-3 alpha-40 quantitative Reynolds checks"
        metadata["Author"] = "Post-processing of completed MFC data"
        metadata["Subject"] = "Reynolds, resolution, and late-time diagnostics"

    assessment = output / "READ_ME_FIRST.md"
    write_assessment(
        assessment,
        root,
        manifest,
        reynolds_summary,
        grid_rows,
        stationarity,
        final_segment,
    )
    report = {
        "status": "PASS",
        "input_dataset": str(root),
        "frames": len(manifest),
        "cases": sorted({str(row["case"]) for row in manifest}),
        "common_grid_reynolds_window": [3.0, 6.0],
        "grid_control": {
            "case": "re1e4_f180_vs_f270",
            "matched_samples": len(grid_rows),
            "time_window": [float(grid_rows[0]["time"]), float(grid_rows[-1]["time"])],
            "means": {
                key: compact_mean(grid_rows, key)
                for key in grid_rows[0]
                if key != "time"
            },
        },
        "re1e6_final_continuous_segment": {
            "time_window": [float(final_segment[0]["time"]), float(final_segment[-1]["time"])],
            "samples": len(final_segment),
            "summaries": stationarity,
        },
        "interpretation": "DESCRIPTIVE_NOT_GRID_CONVERGENCE_OR_STATIONARITY_CERTIFICATE",
        "force_policy": "DO_NOT_INFER_CL_CD_FROM_RESAMPLED_TRAINING_TENSORS",
        "outputs": {},
    }
    for path in (metrics_path, grid_path, reynolds_path, stationarity_path, pdf_path, assessment):
        report["outputs"][path.name] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    report_path = output / "tim_colonius_analysis_report.json"
    report_path.write_text(
        json.dumps(json_ready(report), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    checksum_path = output / "SHA256SUMS.txt"
    checksum_targets = [metrics_path, grid_path, reynolds_path, stationarity_path, pdf_path, assessment, report_path]
    checksum_path.write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in checksum_targets),
        encoding="utf-8",
    )
    marker = output / "ANALYSIS_OK.txt"
    marker.write_text(
        f"status=PASS\nframes={len(manifest)}\npdf_sha256={sha256(pdf_path)}\n",
        encoding="utf-8",
    )
    print(f"TIM_COLONIUS_ANALYSIS=PASS output={output}")
    print(f"SEND={pdf_path}")
    return report


def synthetic_dataset(root: Path) -> None:
    tensor_dir = root / "tensors"
    tensor_dir.mkdir(parents=True)
    names = np.asarray(
        [
            "rho",
            "pressure",
            "u",
            "v",
            "mach",
            "schlieren",
            "omega_z",
            "lambda_ci",
            "q_criterion",
            "omega_ratio",
            "gamma2",
        ]
    )
    x = np.linspace(-1.25, 4.75, 64, dtype=np.float32)
    y = np.linspace(-1.25, 4.75, 64, dtype=np.float32)
    xx, yy = np.meshgrid(x, y, indexing="xy")
    half = 0.0702704174 * (1.0 - np.abs(2.0 * np.clip(xx, 0.0, 1.0) - 1.0))
    body = (xx >= 0.0) & (xx <= 1.0) & (np.abs(yy) <= half)
    fluid = ~body
    definitions = {
        "re1e4_f180": (1.0e4, "f180", np.linspace(0.0, 6.0, 25)),
        "re1e4_f270": (1.0e4, "f270", np.linspace(0.0, 6.0, 25)),
        "re5e4_f180": (5.0e4, "f180", np.linspace(0.0, 6.0, 25)),
        "re1e5_f180": (1.0e5, "f180", np.linspace(0.0, 6.0, 25)),
        "re1e6_retained": (1.0e6, "f270", np.r_[6.0, 21.0, np.linspace(26.0, 31.0, 11)]),
    }
    manifest: list[dict[str, Any]] = []
    shock_rows: list[dict[str, Any]] = []
    global_index = 0
    alpha = math.radians(40.0)
    s = xx * math.cos(alpha) + yy * math.sin(alpha)
    n = -xx * math.sin(alpha) + yy * math.cos(alpha)
    for case, (reynolds, grid, times) in definitions.items():
        fine_factor = 1.2 if grid == "f270" else 1.0
        reynolds_factor = 1.0 + 0.1 * math.log10(reynolds / 1.0e4)
        for sequence_index, time in enumerate(times):
            phase = 2.0 * math.pi * 0.18 * float(time)
            pressure = 1.0 + 0.22 * np.tanh((s + 0.25 + 0.01 * np.sin(phase)) / 0.04)
            pressure += 0.02 * fine_factor * np.sin(12.0 * xx + phase) * np.exp(-((yy - 0.3) / 0.7) ** 2)
            u = 3.0 + 0.08 * np.sin(2.0 * yy + phase)
            v = 0.05 * np.cos(2.0 * xx - phase)
            omega = reynolds_factor * fine_factor * np.sin(5.0 * n - phase) * np.exp(-((s - 1.0) / 2.0) ** 2)
            rho = 1.0 + 0.15 * np.tanh((s + 0.25) / 0.04)
            schlieren = np.hypot(*np.gradient(rho, y, x))
            sound = np.sqrt(1.4 * pressure / rho)
            mach = np.hypot(u, v) / sound
            zeros = np.zeros_like(pressure)
            stack = np.stack([rho, pressure, u, v, mach, schlieren, omega, zeros, zeros, zeros, zeros]).astype(np.float32)
            stack[:, ~fluid] = 0.0
            ridge = (np.abs(s + 0.25) < 0.035) & (np.abs(n) <= 0.24) & fluid
            shock = (np.abs(s + 0.25) < 0.07) & (np.abs(n) <= 0.28) & fluid
            step = int(round(float(time) * 1000))
            identifier = f"{case}_s{step:09d}"
            tensor = tensor_dir / f"{identifier}.npz"
            np.savez_compressed(
                tensor,
                fields=stack,
                field_names=names,
                x=x,
                y=y,
                fluid_mask=fluid.astype(np.uint8),
                shock_mask=shock.astype(np.uint8),
                shock_ridge=ridge.astype(np.uint8),
            )
            manifest.append(
                {
                    "dataset_id": identifier,
                    "global_index": global_index,
                    "sequence_index": sequence_index,
                    "case": case,
                    "Re_c": reynolds,
                    "grid": grid,
                    "time": float(time),
                    "source_step": step,
                    "tensor": str(tensor.relative_to(root)),
                }
            )
            shock_rows.append(
                {
                    "dataset_id": identifier,
                    "status": "PASS",
                    "stand_off_over_c": 0.25 - 0.01 * math.sin(phase),
                }
            )
            global_index += 1
    with (root / "manifest.jsonl").open("w", encoding="utf-8") as stream:
        for row in manifest:
            stream.write(json.dumps(row) + "\n")
    write_csv(root / "shock_catalogue.csv", shock_rows)
    (root / "DATASET_OK.txt").write_text("status=PASS\n", encoding="utf-8")


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="tim-colonius-self-test-") as directory:
        base = Path(directory)
        dataset = base / "dataset"
        output = base / "output"
        dataset.mkdir()
        synthetic_dataset(dataset)
        report = analyze(dataset, output, allow_small=True)
        required = (
            output / "TIM_COLONIUS_QUANTITATIVE_CHECKS.pdf",
            output / "tim_colonius_analysis_report.json",
            output / "ANALYSIS_OK.txt",
        )
        if report["status"] != "PASS" or not all(path.stat().st_size > 0 for path in required):
            raise RuntimeError("end-to-end synthetic analysis failed")
    print("TIM_COLONIUS_SELF_TEST=PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, help="complete Unity ml_dataset directory")
    parser.add_argument("--output", type=Path, help="analysis output directory")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.dataset is None or args.output is None:
        parser.error("--dataset and --output are required unless --self-test is used")
    analyze(args.dataset, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
