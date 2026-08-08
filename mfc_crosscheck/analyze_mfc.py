#!/usr/bin/env python3
"""Publication-oriented analysis for the MFC diamond-airfoil cross-check.

Run this inside the official MFC container.  It uses MFC's own Silo reader to
assemble MPI rank files, masks immersed-boundary cells, derives nondimensional
pressure, temperature, Mach number, and density-gradient magnitude, and
compares the last two saved fields.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mfc-matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


GAMMA = 1.4
MACH_INF = 3.0
RHO_INF = 1.0
P_INF = 1.0 / GAMMA
HALF_ANGLE_DEG = 8.0
AIRFOIL_X = np.array([0.0, 0.5, 1.0, 0.5, 0.0])
AIRFOIL_Y = np.array([0.0, -0.0702704174, 0.0, 0.0702704174, 0.0])


def _theta_from_beta(beta: np.ndarray, mach: float, gamma: float) -> np.ndarray:
    numerator = 2.0 / np.tan(beta) * (mach**2 * np.sin(beta) ** 2 - 1.0)
    denominator = mach**2 * (gamma + np.cos(2.0 * beta)) + 2.0
    return np.arctan(numerator / denominator)


def _theta_max_deg(mach: float, gamma: float) -> float:
    mu = math.asin(1.0 / mach)
    beta = np.linspace(mu + 1.0e-7, math.pi / 2.0 - 1.0e-7, 200_000)
    return float(np.degrees(np.max(_theta_from_beta(beta, mach, gamma))))


def _normal_shock_pressure_ratio(mach: float, gamma: float) -> float:
    return 1.0 + 2.0 * gamma / (gamma + 1.0) * (mach**2 - 1.0)


def _derived(data: SimpleNamespace | object) -> dict[str, np.ndarray]:
    variables = data.variables
    rho = np.asarray(variables["rho"], dtype=float)
    pres = np.asarray(variables["pres"], dtype=float)
    vel1 = np.asarray(variables["vel1"], dtype=float)
    vel2 = np.asarray(variables["vel2"], dtype=float)
    marker = np.asarray(variables.get("ib_markers", np.zeros_like(rho)), dtype=float)

    safe_rho = np.maximum(rho, 1.0e-14)
    safe_pres = np.maximum(pres, 1.0e-14)
    sound_speed = np.sqrt(GAMMA * safe_pres / safe_rho)
    mach = np.sqrt(vel1**2 + vel2**2) / sound_speed
    temperature_ratio = (pres / safe_rho) / (P_INF / RHO_INF)
    pressure_ratio = pres / P_INF
    drho_dx = np.gradient(rho, np.asarray(data.x_cc), axis=0, edge_order=2)
    drho_dy = np.gradient(rho, np.asarray(data.y_cc), axis=1, edge_order=2)
    grad_rho = np.hypot(drho_dx, drho_dy)

    return {
        "rho": rho,
        "pres": pres,
        "pressure_ratio": pressure_ratio,
        "temperature_ratio": temperature_ratio,
        "vel1": vel1,
        "vel2": vel2,
        "mach": mach,
        "grad_rho": grad_rho,
        "schlieren": np.asarray(variables.get("schlieren", np.ones_like(rho))),
        "marker": marker,
        "fluid": marker < 0.5,
    }


def _region_mask(x: np.ndarray, y: np.ndarray, crop: tuple[float, ...]) -> np.ndarray:
    xmin, xmax, ymin, ymax = crop
    return (
        (x[:, None] >= xmin)
        & (x[:, None] <= xmax)
        & (y[None, :] >= ymin)
        & (y[None, :] <= ymax)
    )


def _relative_l2(old: np.ndarray, new: np.ndarray, mask: np.ndarray) -> float:
    valid = mask & np.isfinite(old) & np.isfinite(new)
    if not np.any(valid):
        return float("nan")
    numerator = np.linalg.norm((new - old)[valid])
    denominator = max(np.linalg.norm(new[valid]), 1.0e-30)
    return float(numerator / denominator)


def _limits(field: np.ndarray, mask: np.ndarray, low: float = 0.5, high: float = 99.5) -> tuple[float, float]:
    values = field[mask & np.isfinite(field)]
    if values.size == 0:
        return 0.0, 1.0
    vmin, vmax = np.percentile(values, [low, high])
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        vmin, vmax = float(np.min(values)), float(np.max(values))
    if vmax <= vmin:
        vmax = vmin + 1.0
    return float(vmin), float(vmax)


def _nearest_indices(coords: np.ndarray, targets: np.ndarray) -> np.ndarray:
    right = np.searchsorted(coords, targets, side="left")
    right = np.clip(right, 0, len(coords) - 1)
    left = np.clip(right - 1, 0, len(coords) - 1)
    choose_left = np.abs(targets - coords[left]) <= np.abs(coords[right] - targets)
    return np.where(choose_left, left, right)


def _shock_ray(data: object, fields: dict[str, np.ndarray], alpha_deg: float) -> dict[str, np.ndarray | float]:
    alpha = math.radians(alpha_deg)
    distance = np.linspace(0.05, 1.50, 900)
    x_ray = -distance * math.cos(alpha)
    y_ray = -distance * math.sin(alpha)
    ix = _nearest_indices(np.asarray(data.x_cc), x_ray)
    iy = _nearest_indices(np.asarray(data.y_cc), y_ray)

    grad = fields["grad_rho"][ix, iy]
    fluid = fields["fluid"][ix, iy]
    valid = fluid & np.isfinite(grad)
    if np.any(valid):
        candidates = np.flatnonzero(valid)
        peak_index = int(candidates[np.argmax(grad[valid])])
        stand_off = float(distance[peak_index])
    else:
        peak_index = -1
        stand_off = float("nan")

    return {
        "distance": distance,
        "x": x_ray,
        "y": y_ray,
        "rho": fields["rho"][ix, iy],
        "pressure_ratio": fields["pressure_ratio"][ix, iy],
        "mach": fields["mach"][ix, iy],
        "grad_rho": grad,
        "fluid": fluid,
        "peak_index": float(peak_index),
        "stand_off": stand_off,
    }


def _decorate(ax: plt.Axes, crop: tuple[float, ...], alpha_deg: float) -> None:
    ax.fill(AIRFOIL_X, AIRFOIL_Y, color="black", zorder=12)
    ax.plot(AIRFOIL_X, AIRFOIL_Y, color="white", linewidth=0.45, zorder=13)
    ax.set_xlim(crop[0], crop[1])
    ax.set_ylim(crop[2], crop[3])
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel(r"$x/c$")
    ax.set_ylabel(r"$y/c$")
    alpha = math.radians(alpha_deg)
    start_x = crop[0] + 0.14
    start_y = crop[2] + 0.28
    length = 0.46
    ax.annotate(
        "",
        xy=(start_x + length * math.cos(alpha), start_y + length * math.sin(alpha)),
        xytext=(start_x, start_y),
        arrowprops={"arrowstyle": "->", "lw": 1.5, "color": "black"},
    )
    ax.text(
        start_x,
        start_y - 0.09,
        rf"$M_\infty=3,\ \alpha={alpha_deg:g}^\circ$",
        fontsize=8.5,
        ha="left",
        va="top",
    )


def _plot_field(
    ax: plt.Axes,
    x: np.ndarray,
    y: np.ndarray,
    field: np.ndarray,
    mask: np.ndarray,
    crop: tuple[float, ...],
    alpha_deg: float,
    title: str,
    label: str,
    cmap: str,
    vmin: float | None = None,
    vmax: float | None = None,
) -> None:
    plot_field = np.ma.masked_where(~mask, field)
    if vmin is None or vmax is None:
        vmin, vmax = _limits(field, mask)
    mesh = ax.pcolormesh(x, y, plot_field.T, shading="auto", cmap=cmap, vmin=vmin, vmax=vmax, rasterized=True)
    _decorate(ax, crop, alpha_deg)
    ax.set_title(title, fontsize=10)
    colorbar = ax.figure.colorbar(mesh, ax=ax, pad=0.018, shrink=0.88)
    colorbar.set_label(label)


def _save_fields_figure(
    data: object,
    fields: dict[str, np.ndarray],
    step: int,
    alpha_deg: float,
    crop: tuple[float, ...],
    output: Path,
) -> None:
    x = np.asarray(data.x_cc)
    y = np.asarray(data.y_cc)
    region = _region_mask(x, y, crop)
    mask = fields["fluid"] & region
    log_grad = np.log10(np.maximum(fields["grad_rho"], 1.0e-8))

    panels = [
        (fields["pressure_ratio"], r"Pressure ratio $p/p_\infty$", r"$p/p_\infty$", "turbo"),
        (fields["rho"], r"Density ratio $\rho/\rho_\infty$", r"$\rho/\rho_\infty$", "viridis"),
        (fields["mach"], "Mach number", r"$M$", "turbo"),
        (fields["temperature_ratio"], r"Temperature ratio $T/T_\infty$", r"$T/T_\infty$", "inferno"),
        (log_grad, r"Density-gradient magnitude", r"$\log_{10}(|\nabla\rho|c/\rho_\infty)$", "gray_r"),
        (1.0 - fields["schlieren"], "Native MFC schlieren contrast", r"$1-S$", "gray_r"),
    ]

    fig, axes = plt.subplots(2, 3, figsize=(14.2, 7.6), constrained_layout=True)
    for ax, (field, title, label, cmap) in zip(axes.flat, panels):
        _plot_field(ax, x, y, field, mask, crop, alpha_deg, title, label, cmap)
    fig.suptitle(
        f"MFC Euler (inviscid, slip wall): Mach 3, alpha={alpha_deg:g} deg, step {step}",
        fontsize=13,
        fontweight="bold",
    )
    fig.savefig(output, dpi=300, bbox_inches="tight")
    plt.close(fig)


def _save_convergence_figure(
    old_data: object,
    new_data: object,
    old: dict[str, np.ndarray],
    new: dict[str, np.ndarray],
    old_step: int,
    new_step: int,
    alpha_deg: float,
    crop: tuple[float, ...],
    output: Path,
) -> None:
    x = np.asarray(new_data.x_cc)
    y = np.asarray(new_data.y_cc)
    region = _region_mask(x, y, crop)
    mask = old["fluid"] & new["fluid"] & region

    fig, axes = plt.subplots(2, 3, figsize=(14.2, 8.0), constrained_layout=True)
    rows = [
        ("pressure_ratio", r"$p/p_\infty$", r"$\Delta(p/p_\infty)$"),
        ("rho", r"$\rho/\rho_\infty$", r"$\Delta(\rho/\rho_\infty)$"),
    ]
    for row, (name, label, difference_label) in enumerate(rows):
        vmin, vmax = _limits(np.concatenate([old[name][mask], new[name][mask]]), np.ones(2 * np.count_nonzero(mask), dtype=bool))
        _plot_field(axes[row, 0], x, y, old[name], mask, crop, alpha_deg, f"{label}, step {old_step}", label, "turbo", vmin, vmax)
        _plot_field(axes[row, 1], x, y, new[name], mask, crop, alpha_deg, f"{label}, step {new_step}", label, "turbo", vmin, vmax)
        difference = new[name] - old[name]
        finite = np.abs(difference[mask & np.isfinite(difference)])
        limit = float(np.percentile(finite, 99.5)) if finite.size else 1.0
        limit = max(limit, 1.0e-12)
        _plot_field(
            axes[row, 2],
            x,
            y,
            difference,
            mask,
            crop,
            alpha_deg,
            f"Change: step {new_step} - {old_step}",
            difference_label,
            "RdBu_r",
            -limit,
            limit,
        )
    fig.suptitle("Saved-field stationarity check (same color limits by row)", fontsize=13, fontweight="bold")
    fig.savefig(output, dpi=300, bbox_inches="tight")
    plt.close(fig)


def _save_ray_figure(ray: dict[str, np.ndarray | float], output: Path) -> None:
    distance = np.asarray(ray["distance"])
    fig, axes = plt.subplots(3, 1, figsize=(7.2, 8.5), sharex=True, constrained_layout=True)
    axes[0].plot(distance, np.asarray(ray["pressure_ratio"]), color="#b2182b", lw=1.7)
    axes[0].set_ylabel(r"$p/p_\infty$")
    axes[1].plot(distance, np.asarray(ray["rho"]), color="#2166ac", lw=1.7)
    axes[1].set_ylabel(r"$\rho/\rho_\infty$")
    axes[2].plot(distance, np.asarray(ray["grad_rho"]), color="black", lw=1.5)
    axes[2].set_ylabel(r"$|\nabla\rho|$")
    axes[2].set_xlabel(r"Upstream distance from leading edge, $s/c$")
    stand_off = float(ray["stand_off"])
    if np.isfinite(stand_off):
        for ax in axes:
            ax.axvline(stand_off, color="#d95f02", ls="--", lw=1.2)
        axes[0].set_title(f"Upstream freestream ray; gradient peak at s/c={stand_off:.4f}")
    for ax in axes:
        ax.grid(alpha=0.22)
    fig.savefig(output, dpi=300, bbox_inches="tight")
    plt.close(fig)


def _write_ray_csv(ray: dict[str, np.ndarray | float], output: Path) -> None:
    columns = np.column_stack(
        [
            ray["distance"],
            ray["x"],
            ray["y"],
            ray["rho"],
            ray["pressure_ratio"],
            ray["mach"],
            ray["grad_rho"],
            np.asarray(ray["fluid"], dtype=int),
        ]
    )
    np.savetxt(
        output,
        columns,
        delimiter=",",
        header="s_over_c,x_over_c,y_over_c,rho_ratio,p_ratio,mach,grad_rho,fluid",
        comments="",
    )


def _analyze(
    old_data: object,
    new_data: object,
    old_step: int,
    new_step: int,
    alpha_deg: float,
    crop: tuple[float, ...],
    output_dir: Path,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    old = _derived(old_data)
    new = _derived(new_data)
    x = np.asarray(new_data.x_cc)
    y = np.asarray(new_data.y_cc)
    region = _region_mask(x, y, crop)
    compare_mask = old["fluid"] & new["fluid"] & region

    convergence = {
        name: _relative_l2(old[name], new[name], compare_mask)
        for name in ("rho", "pres", "vel1", "vel2", "mach")
    }
    max_change = max(value for value in convergence.values() if np.isfinite(value))
    stationarity_threshold = 5.0e-3
    stationarity = "PASS" if max_change <= stationarity_threshold else "CONTINUE_REQUIRED"

    farfield_mask = new["fluid"] & (x[:, None] < -2.0)
    alpha = math.radians(alpha_deg)
    expected = {
        "rho": RHO_INF,
        "pres": P_INF,
        "vel1": MACH_INF * math.cos(alpha),
        "vel2": MACH_INF * math.sin(alpha),
    }
    farfield = {
        name: float(np.median(new[name][farfield_mask]))
        for name in ("rho", "pres", "vel1", "vel2")
    }
    farfield_relative_error = {
        name: abs(farfield[name] - expected[name]) / max(abs(expected[name]), 1.0e-30)
        for name in expected
    }

    fluid_region = new["fluid"] & region
    ray = _shock_ray(new_data, new, alpha_deg)
    theta_windward = alpha_deg + HALF_ANGLE_DEG
    theta_max = _theta_max_deg(MACH_INF, GAMMA)

    metrics: dict[str, object] = {
        "solver_regime": "Euler (inviscid, slip-wall immersed boundary)",
        "mach_infinity": MACH_INF,
        "alpha_deg": alpha_deg,
        "diamond_half_angle_deg": HALF_ANGLE_DEG,
        "windward_turn_deg": theta_windward,
        "mach3_attached_shock_limit_deg": theta_max,
        "detached_shock_expected": theta_windward > theta_max,
        "normal_shock_pressure_ratio_reference": _normal_shock_pressure_ratio(MACH_INF, GAMMA),
        "old_step": old_step,
        "new_step": new_step,
        "relative_l2_change_near_body": convergence,
        "stationarity_threshold": stationarity_threshold,
        "stationarity_assessment": stationarity,
        "farfield_median": farfield,
        "farfield_expected": expected,
        "farfield_relative_error": farfield_relative_error,
        "fluid_near_body_extrema": {
            "pressure_ratio_min": float(np.min(new["pressure_ratio"][fluid_region])),
            "pressure_ratio_max": float(np.max(new["pressure_ratio"][fluid_region])),
            "rho_ratio_min": float(np.min(new["rho"][fluid_region])),
            "rho_ratio_max": float(np.max(new["rho"][fluid_region])),
            "mach_min": float(np.min(new["mach"][fluid_region])),
            "mach_max": float(np.max(new["mach"][fluid_region])),
        },
        "estimated_shock_standoff_over_c": float(ray["stand_off"]),
        "crop": list(crop),
    }

    _save_fields_figure(new_data, new, new_step, alpha_deg, crop, output_dir / "mfc_fields_closeup.png")
    _save_convergence_figure(
        old_data,
        new_data,
        old,
        new,
        old_step,
        new_step,
        alpha_deg,
        crop,
        output_dir / "mfc_saved_field_change.png",
    )
    _save_ray_figure(ray, output_dir / "mfc_shock_ray.png")
    _write_ray_csv(ray, output_dir / "mfc_shock_ray.csv")

    with (output_dir / "mfc_metrics.json").open("w", encoding="utf-8") as stream:
        json.dump(metrics, stream, indent=2, sort_keys=True)
        stream.write("\n")

    summary = [
        "MFC DIAMOND-AIRFOIL VALIDATION SUMMARY",
        "======================================",
        f"Regime: {metrics['solver_regime']}",
        f"Saved-field comparison: {old_step} -> {new_step}",
        f"Stationarity assessment: {stationarity} (threshold={stationarity_threshold:.3e})",
    ]
    for name, value in convergence.items():
        summary.append(f"  relative L2 change {name:>5s}: {value:.6e}")
    summary.extend(
        [
            f"Windward turn: {theta_windward:.4f} deg",
            f"Mach-3 attached-shock limit: {theta_max:.4f} deg",
            f"Detached shock expected: {theta_windward > theta_max}",
            f"Estimated shock stand-off s/c: {float(ray['stand_off']):.6f}",
            f"Reference normal-shock p2/p1: {_normal_shock_pressure_ratio(MACH_INF, GAMMA):.6f}",
            "",
            "Publication decision: do not use loads or stand-off until stationarity,",
            "grid sensitivity, and far-boundary sensitivity have all passed.",
        ]
    )
    (output_dir / "mfc_validation_summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return metrics


def _synthetic_data(shift: float = 0.0) -> SimpleNamespace:
    x = np.linspace(-3.0, 2.8, 460)
    y = np.linspace(-1.6, 1.8, 300)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    alpha = math.radians(30.0)
    normal = xx * math.cos(alpha) + yy * math.sin(alpha) + 0.22 + shift
    shock = 0.5 * (1.0 + np.tanh(normal / 0.025))
    rho = 1.0 + 2.2 * shock * np.exp(-0.18 * ((xx - 0.2) ** 2 + yy**2))
    pres = P_INF * (1.0 + 7.0 * shock * np.exp(-0.2 * ((xx - 0.2) ** 2 + yy**2)))
    vel1 = MACH_INF * math.cos(alpha) - 0.9 * shock
    vel2 = MACH_INF * math.sin(alpha) - 0.5 * shock
    inside = (
        (xx >= 0.0)
        & (xx <= 1.0)
        & (np.abs(yy) <= 0.0702704174 * (1.0 - 2.0 * np.abs(xx - 0.5)))
    )
    marker = inside.astype(float)
    return SimpleNamespace(
        x_cc=x,
        y_cc=y,
        variables={
            "rho": rho,
            "pres": pres,
            "vel1": vel1,
            "vel2": vel2,
            "ib_markers": marker,
            "schlieren": np.exp(-0.1 * np.hypot(np.gradient(rho, x, axis=0), np.gradient(rho, y, axis=1))),
        },
    )


def _load_mfc_data(case_dir: Path, step: int) -> object:
    toolchain = os.environ.get("MFC_TOOLCHAIN", "/opt/MFC/toolchain")
    if toolchain not in sys.path:
        sys.path.insert(0, toolchain)
    try:
        from mfc.viz.silo_reader import assemble_silo
    except ImportError as exc:
        raise RuntimeError(
            "MFC Silo reader is unavailable. Run this script inside the MFC container "
            "or set MFC_TOOLCHAIN to MFC's toolchain directory."
        ) from exc
    return assemble_silo(str(case_dir), step)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", nargs="?", type=Path, help="archived MFC case directory")
    parser.add_argument("--step", type=int, default=1800)
    parser.add_argument("--compare-step", type=int, default=1500)
    parser.add_argument("--alpha", type=float, default=30.0)
    parser.add_argument("--crop", nargs=4, type=float, default=(-1.25, 2.75, -1.50, 1.75))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    crop = tuple(args.crop)
    if args.self_test:
        output = args.output or Path("mfc_analysis_self_test")
        metrics = _analyze(
            _synthetic_data(shift=0.015),
            _synthetic_data(shift=0.0),
            args.compare_step,
            args.step,
            args.alpha,
            crop,
            output,
        )
    else:
        if args.case_dir is None:
            parser.error("case_dir is required unless --self-test is used")
        case_dir = args.case_dir.resolve()
        output = args.output or case_dir / "analysis"
        old_data = _load_mfc_data(case_dir, args.compare_step)
        new_data = _load_mfc_data(case_dir, args.step)
        metrics = _analyze(
            old_data,
            new_data,
            args.compare_step,
            args.step,
            args.alpha,
            crop,
            output,
        )

    print(json.dumps(metrics, indent=2, sort_keys=True))
    print(f"Analysis written to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
