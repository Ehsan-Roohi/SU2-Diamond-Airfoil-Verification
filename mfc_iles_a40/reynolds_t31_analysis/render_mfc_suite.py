#!/usr/bin/env python3
"""Render matched Reynolds-number fields and long-HLL movies from MFC data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mfc-reynolds-t31-matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.animation as animation
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon


AIRFOIL = np.array([[0.0, 0.0], [0.5, 0.0702704174], [1.0, 0.0], [0.5, -0.0702704174]])
ALPHA_DEG = 40.0
U_INF = 3.0
RHO_INF = 1.0
GAMMA = 1.4
P_INF = 1.0 / GAMMA
VIEW = (-1.25, 4.75, -1.25, 4.75)
GRAD_MAX = 65.0
VORT_MAX = 17.0
PRIMARY_LABELS = ("re1e4_f270", "re5e4_f180", "re1e5_f180", "re1e6_f270")


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


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def configure_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable is None:
        try:
            import imageio_ffmpeg

            executable = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError as exc:
            raise RuntimeError("ffmpeg or imageio_ffmpeg is required for movies") from exc
    if not Path(executable).is_file():
        raise RuntimeError(f"ffmpeg executable is unavailable: {executable}")
    matplotlib.rcParams["animation.ffmpeg_path"] = executable
    return executable


def read_cases(path: Path) -> list[CaseInfo]:
    rows: list[CaseInfo] = []
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            rows.append(
                CaseInfo(
                    label=row["label"],
                    display=row["display"],
                    reynolds=float(row["reynolds"]),
                    grid=row["grid"],
                    dt=float(row["dt"]),
                    case_dir=Path(row["case_dir"]).resolve(),
                    analysis_start=float(row["analysis_start"]),
                    role=row["role"],
                )
            )
    labels = [row.label for row in rows]
    if len(labels) != len(set(labels)):
        raise RuntimeError("duplicate labels in case table")
    required = set(PRIMARY_LABELS) | {"re1e4_f180", "re1e6_long_t31"}
    missing = sorted(required - set(labels))
    if missing:
        raise RuntimeError(f"case table lacks required labels: {missing}")
    for row in rows:
        if not (row.case_dir / "restart_data").is_dir():
            raise RuntimeError(f"missing restart_data for {row.label}: {row.case_dir}")
    return rows


def step_from_name(path: Path) -> int:
    match = re.fullmatch(r"lustre_(\d+)\.dat", path.name)
    if match is None:
        raise ValueError(path)
    return int(match.group(1))


def available_steps(case: CaseInfo) -> set[int]:
    return {
        step_from_name(path)
        for path in (case.case_dir / "restart_data").glob("lustre_[0-9]*.dat")
    }


def as_xy(values: np.ndarray, nx: int, ny: int, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.shape == (nx, ny):
        return array
    if array.shape == (ny, nx):
        return array.T
    raise RuntimeError(f"unexpected {name} shape {array.shape}; expected {(nx, ny)}")


def body_mask(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    xx = x[:, None]
    yy = y[None, :]
    clipped = np.clip(xx, 0.0, 1.0)
    half_height = 0.0702704174 * (1.0 - np.abs(2.0 * clipped - 1.0))
    return (xx >= 0.0) & (xx <= 1.0) & (np.abs(yy) <= half_height)


def load_fields(case: CaseInfo, step: int, assemble: Any) -> dict[str, Any]:
    source = case.case_dir / "restart_data" / f"lustre_{step}.dat"
    if not source.is_file() or source.stat().st_size == 0:
        raise RuntimeError(f"missing field for {case.label} at step {step}: {source}")
    assembled = assemble(str(case.case_dir), int(step), fmt="binary")
    needed = {"rho", "pres", "vel1", "vel2"}
    missing = sorted(needed - set(assembled.variables))
    if missing:
        raise RuntimeError(f"{case.label} step {step} lacks variables {missing}")
    x_all = np.asarray(assembled.x_cc, dtype=float)
    y_all = np.asarray(assembled.y_cc, dtype=float)
    nx, ny = len(x_all), len(y_all)
    rho_all = as_xy(assembled.variables["rho"], nx, ny, "rho")
    pressure_all = as_xy(assembled.variables["pres"], nx, ny, "pres")
    u_all = as_xy(assembled.variables["vel1"], nx, ny, "vel1")
    v_all = as_xy(assembled.variables["vel2"], nx, ny, "vel2")
    if not all(np.isfinite(field).all() for field in (rho_all, pressure_all, u_all, v_all)):
        raise RuntimeError(f"non-finite primitive field in {case.label} at step {step}")

    xmin, xmax, ymin, ymax = VIEW
    ix = np.flatnonzero((x_all >= xmin) & (x_all <= xmax))
    iy = np.flatnonzero((y_all >= ymin) & (y_all <= ymax))
    if len(ix) < 8 or len(iy) < 8:
        raise RuntimeError("fixed comparison view does not intersect the grid")
    halo = 3
    xs = slice(max(int(ix[0]) - halo, 0), min(int(ix[-1]) + halo + 1, nx))
    ys = slice(max(int(iy[0]) - halo, 0), min(int(iy[-1]) + halo + 1, ny))
    x = x_all[xs]
    y = y_all[ys]
    rho = np.array(rho_all[xs, ys], dtype=np.float32, copy=True)
    pressure = np.array(pressure_all[xs, ys], dtype=np.float32, copy=True)
    u = np.array(u_all[xs, ys], dtype=np.float32, copy=True)
    v = np.array(v_all[xs, ys], dtype=np.float32, copy=True)
    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    drdx, drdy = np.gradient(rho, dx, dy, edge_order=2)
    dudx, dudy = np.gradient(u, dx, dy, edge_order=2)
    dvdx, dvdy = np.gradient(v, dx, dy, edge_order=2)
    del dudx, dvdy
    gradient = np.hypot(drdx, drdy).astype(np.float32)
    vorticity = (dvdx - dudy).astype(np.float32)
    acoustic = np.sqrt(np.maximum(GAMMA * pressure / np.maximum(rho, 1.0e-12), 1.0e-12))
    mach = (np.hypot(u, v) / acoustic).astype(np.float32)
    cp = ((pressure - P_INF) / (0.5 * RHO_INF * U_INF**2)).astype(np.float32)
    solid = body_mask(x, y)
    for field in (rho, pressure, u, v, gradient, vorticity, mach, cp):
        field[solid] = np.nan
    return {
        "x": x,
        "y": y,
        "rho": rho,
        "pressure": pressure,
        "u": u,
        "v": v,
        "gradient": gradient,
        "vorticity": vorticity,
        "mach": mach,
        "cp": cp,
        "step": int(step),
        "time": float(step * case.dt),
        "source": str(source.resolve()),
    }


def bilinear(x: np.ndarray, y: np.ndarray, field: np.ndarray, xp: np.ndarray, yp: np.ndarray) -> np.ndarray:
    ix = np.clip(np.searchsorted(x, xp, side="right") - 1, 0, len(x) - 2)
    iy = np.clip(np.searchsorted(y, yp, side="right") - 1, 0, len(y) - 2)
    wx = (xp - x[ix]) / (x[ix + 1] - x[ix])
    wy = (yp - y[iy]) / (y[iy + 1] - y[iy])
    return (
        (1.0 - wx) * (1.0 - wy) * field[ix, iy]
        + wx * (1.0 - wy) * field[ix + 1, iy]
        + (1.0 - wx) * wy * field[ix, iy + 1]
        + wx * wy * field[ix + 1, iy + 1]
    )


def field_metrics(case: CaseInfo, fields: dict[str, Any]) -> dict[str, Any]:
    alpha = math.radians(ALPHA_DEG)
    cs, sn = math.cos(alpha), math.sin(alpha)
    x = fields["x"]
    y = fields["y"]
    u_stream = fields["u"] * cs + fields["v"] * sn
    xx, yy = np.meshgrid(x, y, indexing="ij")
    rel_x = xx - 1.0
    rel_y = yy
    streamwise = rel_x * cs + rel_y * sn
    normal = -rel_x * sn + rel_y * cs
    wake = (streamwise >= 0.05) & (streamwise <= 3.0) & (np.abs(normal) <= 0.75)
    wake_valid = wake & np.isfinite(fields["vorticity"]) & np.isfinite(u_stream)

    sample_s = np.linspace(0.02, 3.0, 600)
    sample_x = 1.0 + sample_s * cs
    sample_y = sample_s * sn
    center_velocity = bilinear(x, y, u_stream, sample_x, sample_y) / U_INF
    reverse = np.isfinite(center_velocity) & (center_velocity < 0.0)
    reverse_extent = float(sample_s[np.flatnonzero(reverse)[-1]]) if np.any(reverse) else 0.0
    omega = np.abs(fields["vorticity"][wake_valid])
    grad = fields["gradient"][wake_valid]
    return {
        "label": case.label,
        "display": case.display,
        "Re_c": case.reynolds,
        "grid": case.grid,
        "step": fields["step"],
        "time": fields["time"],
        "centerline_reverse_flow_extent_over_c": reverse_extent,
        "wake_vorticity_abs_p95": float(np.nanpercentile(omega, 95.0)),
        "wake_vorticity_abs_p99": float(np.nanpercentile(omega, 99.0)),
        "wake_enstrophy_mean": float(np.nanmean(omega**2)),
        "wake_fraction_abs_vorticity_gt_5": float(np.mean(omega > 5.0)),
        "wake_density_gradient_p99": float(np.nanpercentile(grad, 99.0)),
        "definitions": {
            "reverse_flow": "last u_parallel<0 point on the trailing-edge freestream ray, 0.02<=s/c<=3",
            "wake_region": "0.05<=s/c<=3 and |n/c|<=0.75 in freestream-aligned coordinates",
            "vorticity_units": "raw nondimensional MFC solver units; no extra U_inf rescaling",
            "screening_warning": "vorticity/enstrophy measures are resolution-sensitive and are not grid-convergence claims",
        },
    }


def add_airfoil(ax: plt.Axes) -> None:
    ax.add_patch(Polygon(AIRFOIL, closed=True, facecolor="black", edgecolor="white", linewidth=0.8, zorder=5))
    ax.set_xlim(VIEW[0], VIEW[1])
    ax.set_ylim(VIEW[2], VIEW[3])
    ax.set_aspect("equal")
    ax.set_xlabel(r"$x/c$")
    ax.set_ylabel(r"$y/c$")


def save_primary_figure(
    output: Path,
    cases: list[CaseInfo],
    fields: dict[str, dict[str, Any]],
    key: str,
    filename: str,
    cmap: str,
    vmin: float,
    vmax: float,
    color_label: str,
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(13.8, 12.0), constrained_layout=True)
    image = None
    for ax, case in zip(axes.flat, cases):
        item = fields[case.label]
        image = ax.pcolormesh(item["x"], item["y"], item[key].T, shading="auto", cmap=cmap, vmin=vmin, vmax=vmax)
        add_airfoil(ax)
        ax.set_title(f"{case.display} ({case.grid}), t={item['time']:.1f}")
    assert image is not None
    fig.colorbar(image, ax=axes, shrink=0.82, label=color_label)
    fig.suptitle("MFC Mach 3, alpha=40 deg: matched final-time Reynolds screening", fontsize=16, fontweight="bold")
    fig.savefig(output / filename, dpi=220, facecolor="white")
    plt.close(fig)


def save_re1e4_grid_figure(output: Path, cases: list[CaseInfo], fields: dict[str, dict[str, Any]]) -> None:
    selected = [next(row for row in cases if row.label == label) for label in ("re1e4_f180", "re1e4_f270")]
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 10.0), constrained_layout=True)
    images = [None, None]
    for row_index, case in enumerate(selected):
        item = fields[case.label]
        images[0] = axes[row_index, 0].pcolormesh(
            item["x"], item["y"], item["gradient"].T, shading="auto", cmap="gray", vmin=0, vmax=GRAD_MAX
        )
        images[1] = axes[row_index, 1].pcolormesh(
            item["x"], item["y"], item["vorticity"].T, shading="auto", cmap="RdBu_r", vmin=-VORT_MAX, vmax=VORT_MAX
        )
        for col in range(2):
            add_airfoil(axes[row_index, col])
            axes[row_index, col].set_title(f"{case.grid}: " + (r"$|\nabla\rho|$" if col == 0 else r"$\omega_z$"))
    fig.colorbar(images[0], ax=axes[:, 0], shrink=0.82, label=r"$|\nabla\rho|c/\rho_\infty$")
    fig.colorbar(images[1], ax=axes[:, 1], shrink=0.82, label=r"$\omega_z$ (solver units)")
    fig.suptitle(r"Grid sensitivity at $Re_c=10^4$, $t=6$", fontsize=16, fontweight="bold")
    fig.savefig(output / "re1e4_f180_f270_grid_check.png", dpi=220, facecolor="white")
    plt.close(fig)


def save_long_final_figure(output: Path, fields: dict[str, Any]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(15.5, 7.2), constrained_layout=True)
    grad = axes[0].pcolormesh(
        fields["x"], fields["y"], fields["gradient"].T,
        shading="auto", cmap="gray", vmin=0.0, vmax=GRAD_MAX,
    )
    vort = axes[1].pcolormesh(
        fields["x"], fields["y"], fields["vorticity"].T,
        shading="auto", cmap="RdBu_r", vmin=-VORT_MAX, vmax=VORT_MAX,
    )
    for ax, title in zip(axes, (r"Density-gradient magnitude $|\nabla\rho|$", r"Spanwise vorticity $\omega_z$")):
        add_airfoil(ax)
        ax.set_title(title)
    fig.colorbar(grad, ax=axes[0], fraction=0.045, pad=0.02, label=r"$|\nabla\rho|c/\rho_\infty$")
    fig.colorbar(vort, ax=axes[1], fraction=0.045, pad=0.02, label=r"$\omega_z$ (solver units)")
    fig.suptitle(r"Long MFC HLL baseline at $Re_c=10^6$, $t=31$", fontsize=16, fontweight="bold")
    fig.savefig(output / "hll_t31_final_schlieren_vorticity.png", dpi=220, facecolor="white")
    plt.close(fig)


def movie_writer(fps: int = 20) -> animation.FFMpegWriter:
    return animation.FFMpegWriter(
        fps=fps,
        codec="libx264",
        bitrate=6000,
        extra_args=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
    )


def new_reynolds_movie_figure(
    cases: list[CaseInfo], cmap: str, vmin: float, vmax: float, color_label: str
) -> tuple[plt.Figure, list[Any], Any]:
    fig, axes = plt.subplots(2, 2, figsize=(13.6, 12.0), constrained_layout=True)
    images: list[Any] = []
    empty = np.zeros((32, 32), dtype=np.float32)
    for ax, case in zip(axes.flat, cases):
        image = ax.imshow(empty, origin="lower", extent=VIEW, cmap=cmap, vmin=vmin, vmax=vmax)
        images.append(image)
        add_airfoil(ax)
        ax.set_title(f"{case.display} ({case.grid})")
    fig.colorbar(images[-1], ax=axes, shrink=0.82, label=color_label)
    return fig, images, fig.suptitle("", fontsize=16, fontweight="bold")


def render_reynolds_movies(
    output: Path,
    cases: list[CaseInfo],
    assemble: Any,
    fps: int,
) -> int:
    times = np.arange(0.0, 6.0 + 0.05, 0.1)
    available = {case.label: available_steps(case) for case in cases}
    for case in cases:
        missing = [round(time / case.dt) for time in times if round(time / case.dt) not in available[case.label]]
        if missing:
            raise RuntimeError(f"{case.label} missing common movie steps {missing[:5]}")
    grad_target = output / "MFC_REYNOLDS_T00_T06_SCHLIEREN.mp4"
    vort_target = output / "MFC_REYNOLDS_T00_T06_VORTICITY.mp4"
    grad_fig, grad_images, grad_title = new_reynolds_movie_figure(
        cases, "gray", 0.0, GRAD_MAX, r"$|\nabla\rho|c/\rho_\infty$"
    )
    vort_fig, vort_images, vort_title = new_reynolds_movie_figure(
        cases, "RdBu_r", -VORT_MAX, VORT_MAX, r"$\omega_z$ (solver units)"
    )
    grad_writer = movie_writer(fps)
    vort_writer = movie_writer(fps)
    with grad_writer.saving(grad_fig, str(grad_target), dpi=125), vort_writer.saving(
        vort_fig, str(vort_target), dpi=125
    ):
        for index, time in enumerate(times):
            for case_index, case in enumerate(cases):
                item = load_fields(case, round(time / case.dt), assemble)
                extent = (item["x"][0], item["x"][-1], item["y"][0], item["y"][-1])
                grad_images[case_index].set_data(item["gradient"].T)
                grad_images[case_index].set_extent(extent)
                vort_images[case_index].set_data(item["vorticity"].T)
                vort_images[case_index].set_extent(extent)
            grad_title.set_text(r"MFC Mach 3, $\alpha=40^\circ$: schlieren" + f", t={time:.1f}")
            vort_title.set_text(r"MFC Mach 3, $\alpha=40^\circ$: vorticity" + f", t={time:.1f}")
            grad_writer.grab_frame(facecolor="white")
            vort_writer.grab_frame(facecolor="white")
            print(f"REYNOLDS_FRAME {index + 1}/{len(times)} t={time:.1f}", flush=True)
    plt.close(grad_fig)
    plt.close(vort_fig)
    for target in (grad_target, vort_target):
        if target.stat().st_size < 1_000_000:
            raise RuntimeError(f"movie is unexpectedly small: {target}")
    return len(times)


def render_long_movie(output: Path, case: CaseInfo, assemble: Any, fps: int) -> int:
    steps = available_steps(case)
    wanted = list(range(round(0.0 / case.dt), round(31.0 / case.dt) + 1, round(0.1 / case.dt)))
    missing = [step for step in wanted if step not in steps]
    if missing:
        raise RuntimeError(f"long view lacks {len(missing)} movie frames: {missing[:8]}")
    target = output / "MFC_HLL_T00_T31_SCHLIEREN_VORTICITY.mp4"
    fig, axes = plt.subplots(1, 2, figsize=(15.5, 7.6), constrained_layout=True)
    empty = np.zeros((32, 32), dtype=np.float32)
    im_grad = axes[0].imshow(empty, origin="lower", extent=VIEW, cmap="gray", vmin=0, vmax=GRAD_MAX)
    im_vort = axes[1].imshow(empty, origin="lower", extent=VIEW, cmap="RdBu_r", vmin=-VORT_MAX, vmax=VORT_MAX)
    for ax, title in zip(axes, (r"Density-gradient magnitude $|\nabla\rho|$", r"Spanwise vorticity $\omega_z$")):
        add_airfoil(ax)
        ax.set_title(title)
    fig.colorbar(im_grad, ax=axes[0], fraction=0.045, pad=0.02)
    fig.colorbar(im_vort, ax=axes[1], fraction=0.045, pad=0.02, label=r"$\omega_z$ (solver units)")
    title = fig.suptitle("", fontsize=16, fontweight="bold")
    writer = movie_writer(fps)
    # 15.5x7.6 inches at 120 dpi gives an even 1860x912 H.264 frame.
    with writer.saving(fig, str(target), dpi=120):
        for index, step in enumerate(wanted):
            item = load_fields(case, step, assemble)
            extent = (item["x"][0], item["x"][-1], item["y"][0], item["y"][-1])
            im_grad.set_data(item["gradient"].T)
            im_grad.set_extent(extent)
            im_vort.set_data(item["vorticity"].T)
            im_vort.set_extent(extent)
            title.set_text(r"MFC HLL, Mach 3, $\alpha=40^\circ$" + f", t={item['time']:.1f}")
            writer.grab_frame(facecolor="white")
            print(f"LONG_FRAME {index + 1}/{len(wanted)} t={item['time']:.1f}", flush=True)
    plt.close(fig)
    if target.stat().st_size < 1_000_000:
        raise RuntimeError(f"movie is unexpectedly small: {target}")
    return len(wanted)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys = [key for key in rows[0] if key != "definitions"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in keys} for row in rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-table", type=Path, required=True)
    parser.add_argument("--mfc-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--skip-movies", action="store_true")
    args = parser.parse_args()
    if args.fps <= 0:
        parser.error("--fps must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = read_cases(args.case_table.resolve())
    by_label = {case.label: case for case in cases}
    primary = [by_label[label] for label in PRIMARY_LABELS]

    mfc_root = args.mfc_root.resolve()
    sys.path.insert(0, str(mfc_root / "toolchain"))
    try:
        from mfc.viz.reader import assemble
    except ImportError as exc:
        raise RuntimeError(f"cannot import the pinned MFC field reader from {mfc_root}") from exc

    final_cases = [by_label["re1e4_f180"], *primary]
    final_fields: dict[str, dict[str, Any]] = {}
    metrics: list[dict[str, Any]] = []
    for case in final_cases:
        step = max(available_steps(case))
        item = load_fields(case, step, assemble)
        if not math.isclose(item["time"], 6.0, abs_tol=1.0e-8):
            raise RuntimeError(f"{case.label} final comparison time is {item['time']}, not 6")
        final_fields[case.label] = item
        metrics.append(field_metrics(case, item))
        print(f"FINAL_FIELD=PASS case={case.label} step={step}", flush=True)

    long_case = by_label["re1e6_long_t31"]
    long_step = max(available_steps(long_case))
    long_final = load_fields(long_case, long_step, assemble)
    if not math.isclose(long_final["time"], 31.0, abs_tol=1.0e-8):
        raise RuntimeError(f"long HLL final time is {long_final['time']}, not 31")
    metrics.append(field_metrics(long_case, long_final))

    save_primary_figure(output, primary, final_fields, "gradient", "reynolds_final_schlieren.png", "gray", 0, GRAD_MAX, r"$|\nabla\rho|c/\rho_\infty$")
    save_primary_figure(output, primary, final_fields, "vorticity", "reynolds_final_vorticity.png", "RdBu_r", -VORT_MAX, VORT_MAX, r"$\omega_z$ (solver units)")
    save_primary_figure(output, primary, final_fields, "mach", "reynolds_final_mach.png", "viridis", 0, 5.0, r"Mach number")
    save_primary_figure(output, primary, final_fields, "cp", "reynolds_final_pressure_coefficient.png", "coolwarm", -0.5, 3.0, r"$C_p$")
    save_re1e4_grid_figure(output, cases, final_fields)
    save_long_final_figure(output, long_final)
    (output / "mfc_reynolds_field_metrics.json").write_text(
        json.dumps({"status": "PASS", "metrics": metrics}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_csv(output / "mfc_reynolds_field_metrics.csv", metrics)

    movie_report: dict[str, Any] = {"status": "SKIPPED"}
    if not args.skip_movies:
        ffmpeg = configure_ffmpeg()
        reynolds_frames = render_reynolds_movies(output, primary, assemble, args.fps)
        long_frames = render_long_movie(output, by_label["re1e6_long_t31"], assemble, args.fps)
        movie_report = {
            "status": "PASS",
            "ffmpeg": ffmpeg,
            "fps": args.fps,
            "reynolds_frames_per_movie": reynolds_frames,
            "long_frames": long_frames,
            "movies": {},
        }
        for path in sorted(output.glob("*.mp4")):
            movie_report["movies"][path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            (output / f"{path.name}.sha256.txt").write_text(
                f"{movie_report['movies'][path.name]['sha256']}  {path.name}\n", encoding="utf-8"
            )
    (output / "movie_manifest.json").write_text(
        json.dumps(movie_report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "VISUALIZATION_OK.txt").write_text(
        f"status=PASS\nmovies={movie_report['status']}\nfield_cases={len(metrics)}\n",
        encoding="utf-8",
    )
    print(f"MFC_SUITE_VISUALIZATION=PASS output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
