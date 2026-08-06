#!/usr/bin/env python3
"""Rebuild the report's Euler figures and validation tables from SU2 output.

The script uses native SU2 restart values and native mesh connectivity for
field plots and shock-gradient fits.  Rectangular interpolation is used only
for the common-scale teaching panels and never for quantitative wave metrics.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri
import numpy as np
from matplotlib.colors import BoundaryNorm
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import brentq


GAMMA = 1.4
M_INF = 3.0
EPS_DEG = 8.0
EPS = math.radians(EPS_DEG)
H = 0.5 * math.tan(EPS)
Q_INF = 0.5 * GAMMA * M_INF**2


@dataclass
class FlowField:
    case: str
    alpha: float
    x: np.ndarray
    y: np.ndarray
    rho: np.ndarray
    rhou: np.ndarray
    rhov: np.ndarray
    energy: np.ndarray
    u: np.ndarray
    v: np.ndarray
    pressure: np.ndarray
    temperature: np.ndarray
    mach: np.ndarray
    speed_ratio: np.ndarray


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--source-media", type=Path,
        help="extracted report media used only to preserve the existing laminar/SST comparison panels",
    )
    return parser.parse_args()


def load_restart(path: Path, case: str, alpha: float) -> FlowField:
    data = np.genfromtxt(path, delimiter=",", names=True, dtype=None, encoding=None)
    names = {name.lower(): name for name in data.dtype.names or ()}
    x = np.asarray(data[names["x"]], dtype=float)
    y = np.asarray(data[names["y"]], dtype=float)
    rho = np.asarray(data[names["density"]], dtype=float)
    rhou = np.asarray(data[names["momentum_x"]], dtype=float)
    rhov = np.asarray(data[names["momentum_y"]], dtype=float)
    energy = np.asarray(data[names["energy"]], dtype=float)
    u = rhou / rho
    v = rhov / rho
    pressure = (GAMMA - 1.0) * (energy - 0.5 * (rhou * rhou + rhov * rhov) / rho)
    temperature = pressure / rho
    sound = np.sqrt(np.maximum(GAMMA * pressure / rho, 1.0e-30))
    speed = np.hypot(u, v)
    mach = speed / sound
    speed_inf = M_INF * math.sqrt(GAMMA)
    return FlowField(
        case=case,
        alpha=alpha,
        x=x,
        y=y,
        rho=rho,
        rhou=rhou,
        rhov=rhov,
        energy=energy,
        u=u,
        v=v,
        pressure=pressure,
        temperature=temperature,
        mach=mach,
        speed_ratio=speed / speed_inf,
    )


def read_mesh_triangles(mesh: Path) -> np.ndarray:
    triangles: list[tuple[int, int, int]] = []
    remaining = 0
    with mesh.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.split("%", 1)[0].strip()
            if not line:
                continue
            if remaining == 0:
                if line.upper().startswith("NELEM"):
                    remaining = int(line.split("=", 1)[1])
                continue
            tokens = line.split()
            kind = int(tokens[0])
            if kind == 9:
                a, b, c, d = map(int, tokens[1:5])
                triangles.append((a, b, c))
                triangles.append((a, c, d))
            elif kind == 5:
                triangles.append(tuple(map(int, tokens[1:4])))
            remaining -= 1
            if remaining == 0:
                break
    if remaining != 0 or not triangles:
        raise ValueError(f"Could not read volume cells from {mesh}")
    return np.asarray(triangles, dtype=np.int64)


def triangle_gradient_magnitude(x: np.ndarray, y: np.ndarray, value: np.ndarray, tri: np.ndarray) -> np.ndarray:
    x1, x2, x3 = x[tri[:, 0]], x[tri[:, 1]], x[tri[:, 2]]
    y1, y2, y3 = y[tri[:, 0]], y[tri[:, 1]], y[tri[:, 2]]
    q1, q2, q3 = value[tri[:, 0]], value[tri[:, 1]], value[tri[:, 2]]
    det = (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)
    safe = np.where(np.abs(det) > 1.0e-30, det, np.nan)
    gx = ((q2 - q1) * (y3 - y1) - (q3 - q1) * (y2 - y1)) / safe
    gy = ((x2 - x1) * (q3 - q1) - (x3 - x1) * (q2 - q1)) / safe
    return np.nan_to_num(np.hypot(gx, gy), nan=0.0, posinf=0.0, neginf=0.0)


def theta_beta_residual(beta: float, mach: float, theta: float) -> float:
    left = math.tan(theta)
    right = 2.0 / math.tan(beta) * (
        (mach * mach * math.sin(beta) ** 2 - 1.0)
        / (mach * mach * (GAMMA + math.cos(2.0 * beta)) + 2.0)
    )
    return left - right


def oblique_shock(mach: float, theta_deg: float) -> dict[str, float]:
    theta = math.radians(theta_deg)
    mu = math.asin(1.0 / mach)
    grid = np.linspace(mu + 1.0e-7, math.pi / 2.0 - 1.0e-7, 2000)
    vals = [theta_beta_residual(float(b), mach, theta) for b in grid]
    roots = []
    for b1, b2, f1, f2 in zip(grid[:-1], grid[1:], vals[:-1], vals[1:]):
        if f1 == 0.0 or f1 * f2 < 0.0:
            roots.append(brentq(theta_beta_residual, float(b1), float(b2), args=(mach, theta)))
    if not roots:
        raise ValueError(f"No attached shock for M={mach}, theta={theta_deg}")
    beta = min(roots)
    mn1 = mach * math.sin(beta)
    p2_p1 = 1.0 + 2.0 * GAMMA / (GAMMA + 1.0) * (mn1 * mn1 - 1.0)
    rho2_rho1 = ((GAMMA + 1.0) * mn1 * mn1) / ((GAMMA - 1.0) * mn1 * mn1 + 2.0)
    t2_t1 = p2_p1 / rho2_rho1
    mn2_sq = (1.0 + 0.5 * (GAMMA - 1.0) * mn1 * mn1) / (GAMMA * mn1 * mn1 - 0.5 * (GAMMA - 1.0))
    m2 = math.sqrt(mn2_sq) / math.sin(beta - theta)
    return {
        "beta_deg": math.degrees(beta),
        "p_ratio": p2_p1,
        "rho_ratio": rho2_rho1,
        "t_ratio": t2_t1,
        "mach": m2,
    }


def prandtl_meyer(mach: float) -> float:
    return math.sqrt((GAMMA + 1.0) / (GAMMA - 1.0)) * math.atan(
        math.sqrt((GAMMA - 1.0) / (GAMMA + 1.0) * (mach * mach - 1.0))
    ) - math.atan(math.sqrt(mach * mach - 1.0))


def expand_state(upstream: dict[str, float], turn_deg: float) -> dict[str, float]:
    target = prandtl_meyer(upstream["mach"]) + math.radians(turn_deg)
    m3 = brentq(lambda m: prandtl_meyer(m) - target, upstream["mach"] + 1.0e-8, 20.0)
    ratio = (1.0 + 0.5 * (GAMMA - 1.0) * upstream["mach"] ** 2) / (
        1.0 + 0.5 * (GAMMA - 1.0) * m3**2
    )
    p3_p2 = ratio ** (GAMMA / (GAMMA - 1.0))
    rho3_rho2 = ratio ** (1.0 / (GAMMA - 1.0))
    return {
        "mach": m3,
        "p_ratio": upstream["p_ratio"] * p3_p2,
        "rho_ratio": upstream["rho_ratio"] * rho3_rho2,
        "t_ratio": upstream["t_ratio"] * ratio,
    }


def theory(alpha: float) -> dict[str, dict[str, float] | float]:
    upper2 = oblique_shock(M_INF, EPS_DEG - alpha)
    lower2 = oblique_shock(M_INF, EPS_DEG + alpha)
    upper3 = expand_state(upper2, 2.0 * EPS_DEG)
    lower3 = expand_state(lower2, 2.0 * EPS_DEG)
    upper_shock_abs = alpha + upper2["beta_deg"]
    lower_shock_abs = alpha - lower2["beta_deg"]
    fan = {
        "upper_trailing": -EPS_DEG + math.degrees(math.asin(1.0 / upper3["mach"])),
        "upper_leading": EPS_DEG + math.degrees(math.asin(1.0 / upper2["mach"])),
        "lower_trailing": EPS_DEG - math.degrees(math.asin(1.0 / lower3["mach"])),
        "lower_leading": -EPS_DEG - math.degrees(math.asin(1.0 / lower2["mach"])),
    }
    return {
        "upper2": upper2,
        "lower2": lower2,
        "upper3": upper3,
        "lower3": lower3,
        "upper_shock_abs": upper_shock_abs,
        "lower_shock_abs": lower_shock_abs,
        "fan": fan,
    }


def theory_loads(alpha: float) -> tuple[float, float]:
    states = theory(abs(alpha))
    if alpha < 0.0:
        upper2, lower2 = states["lower2"], states["upper2"]
        upper3, lower3 = states["lower3"], states["upper3"]
    else:
        upper2, lower2 = states["upper2"], states["lower2"]
        upper3, lower3 = states["upper3"], states["lower3"]
    cps = [
        (lower2["p_ratio"] - 1.0) / Q_INF,
        (lower3["p_ratio"] - 1.0) / Q_INF,
        (upper3["p_ratio"] - 1.0) / Q_INF,
        (upper2["p_ratio"] - 1.0) / Q_INF,
    ]
    vertices = np.array([[0.0, 0.0], [0.5, -H], [1.0, 0.0], [0.5, H], [0.0, 0.0]])
    force = np.zeros(2)
    for cp, a, b in zip(cps, vertices[:-1], vertices[1:]):
        edge = b - a
        ds = float(np.hypot(edge[0], edge[1]))
        outward = np.array([edge[1], -edge[0]]) / ds
        force += -cp * outward * ds
    ar = math.radians(alpha)
    drag = force[0] * math.cos(ar) + force[1] * math.sin(ar)
    lift = force[1] * math.cos(ar) - force[0] * math.sin(ar)
    return float(lift), float(drag)


def sample_state(field: FlowField, branch: str, region: str, shock_abs: float) -> dict[str, float | int]:
    """Sample a uniform panel state without using a rendered wave location.

    The sharp O-grid captures the leading-edge shock accurately in the native
    near-nose fit window, but the discontinuity broadens and bends farther from
    the nose.  A wall-normal band is therefore safer than a fraction of the
    extrapolated shock-to-wall distance for state validation.  The band is
    outside the Euler wall, clear of the discontinuity, and excludes the
    corner cells.
    """
    x, y = field.x, field.y
    if region == "postshock":
        mask_x = (x >= 0.18) & (x <= 0.38)
        surface = (1.0 if branch == "upper" else -1.0) * math.tan(EPS) * x
        normal_distance = (y - surface) if branch == "upper" else (surface - y)
        mask = mask_x & (normal_distance >= 0.006) & (normal_distance <= 0.026)
    else:
        mask_x = (x >= 0.66) & (x <= 0.84)
        surface = (1.0 if branch == "upper" else -1.0) * math.tan(EPS) * (1.0 - x)
        normal_distance = (y - surface) if branch == "upper" else (surface - y)
        mask = mask_x & (normal_distance >= 0.006) & (normal_distance <= 0.026)
    if int(mask.sum()) < 20:
        raise ValueError(f"Too few {branch} {region} samples: {int(mask.sum())}")
    return {
        "p_ratio": float(np.median(field.pressure[mask])),
        "rho_ratio": float(np.median(field.rho[mask])),
        "t_ratio": float(np.median(field.temperature[mask])),
        "mach": float(np.median(field.mach[mask])),
        "samples": int(mask.sum()),
    }


def extract_fan_edges(
    field: FlowField,
    branch: str,
    p2: float,
    p3: float,
    theory_leading: float,
    theory_trailing: float,
) -> dict[str, float]:
    ox, oy = 0.5, H if branch == "upper" else -H
    dx, dy = field.x - ox, field.y - oy
    radius = np.hypot(dx, dy)
    angle = np.degrees(np.arctan2(dy, dx))
    amin = min(theory_leading, theory_trailing) - 5.0
    amax = max(theory_leading, theory_trailing) + 5.0
    use = (radius >= 0.14) & (radius <= 0.34) & (angle >= amin) & (angle <= amax)
    edges = np.linspace(amin, amax, 181)
    centers = 0.5 * (edges[:-1] + edges[1:])
    med = np.full_like(centers, np.nan)
    for i in range(len(centers)):
        take = use & (angle >= edges[i]) & (angle < edges[i + 1])
        if int(take.sum()) >= 3:
            med[i] = np.median(field.pressure[take])
    valid = np.isfinite(med)
    if int(valid.sum()) < 20:
        raise ValueError(f"Too few {branch} fan bins")
    med = np.interp(centers, centers[valid], med[valid])
    med = gaussian_filter1d(med, 1.4)
    fraction = (med - p3) / (p2 - p3)
    central = (centers >= min(theory_leading, theory_trailing) - 2.5) & (
        centers <= max(theory_leading, theory_trailing) + 2.5
    )
    idx = np.where(central)[0]
    a05 = float(centers[idx[np.argmin(np.abs(fraction[idx] - 0.05))]])
    a95 = float(centers[idx[np.argmin(np.abs(fraction[idx] - 0.95))]])
    trailing = a05
    leading = a95
    return {"trailing_deg": trailing, "leading_deg": leading}


def read_history_mean(path: Path, window: int = 200) -> dict[str, float]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) < window:
        raise ValueError(f"{path} has only {len(rows)} rows")
    def key(row: dict[str, str], target: str) -> float:
        for name, value in row.items():
            if name and name.strip().strip('"').upper() == target:
                return float(value)
        raise KeyError(target)
    tail = rows[-window:]
    cl = np.asarray([key(row, "CL") for row in tail])
    cd = np.asarray([key(row, "CD") for row in tail])
    rho_res = np.asarray([key(row, "RMS[RHO]") for row in tail])
    return {
        "cl_mean": float(cl.mean()),
        "cd_mean": float(cd.mean()),
        "cl_ptp": float(np.ptp(cl)),
        "cd_ptp": float(np.ptp(cd)),
        "rho_residual_final": float(rho_res[-1]),
    }


def draw_airfoil(ax: plt.Axes, color: str = "white", edge: str = "black") -> None:
    poly = np.array([[0.0, 0.0], [0.5, H], [1.0, 0.0], [0.5, -H]])
    ax.fill(poly[:, 0], poly[:, 1], color=color, edgecolor=edge, linewidth=0.9, zorder=20)


def plot_four_fields(field: FlowField, tri: np.ndarray, out: Path) -> None:
    triang = mtri.Triangulation(field.x, field.y, tri)
    values = [field.rho, field.mach, field.pressure, field.temperature]
    labels = [
        r"Density ratio, $\rho/\rho_\infty$", r"Mach number, $M$",
        r"Pressure ratio, $p/p_\infty$", r"Temperature ratio, $T/T_\infty$",
    ]
    fig, axes = plt.subplots(2, 2, figsize=(10.8, 9.1), constrained_layout=True)
    for ax, val, label in zip(axes.ravel(), values, labels):
        in_view = (field.x > -0.1) & (field.x < 1.28) & (np.abs(field.y) < 0.72)
        lo, hi = np.nanpercentile(val[in_view], [0.5, 99.5])
        levels = np.linspace(lo, hi, 28)
        handle = ax.tricontourf(
            triang, np.clip(val, lo, hi), levels=levels, cmap="turbo", extend="both"
        )
        draw_airfoil(ax)
        ax.set_xlim(-0.08, 1.25)
        ax.set_ylim(-0.70, 0.70)
        ax.set_xlabel(r"$x/c$")
        ax.set_ylabel(r"$y/c$")
        ax.set_title(label, fontsize=10.5)
        fig.colorbar(handle, ax=ax, shrink=0.86, pad=0.02)
    fig.suptitle(
        fr"SU2 sharp-diamond solution: $M_\infty=3$, $\alpha={field.alpha:.0f}^\circ$",
        fontweight="bold",
    )
    fig.savefig(out, dpi=330, bbox_inches="tight")
    plt.close(fig)


def plot_schlieren(
    field: FlowField,
    tri: np.ndarray,
    metrics: dict[str, object],
    out: Path,
) -> None:
    triang = mtri.Triangulation(field.x, field.y, tri)
    grad = triangle_gradient_magnitude(field.x, field.y, field.rho, tri)
    intensity = np.log10(1.0 + 0.02 * grad)
    tx = field.x[tri].mean(axis=1)
    ty = field.y[tri].mean(axis=1)
    view = (tx > -0.2) & (tx < 1.3) & (np.abs(ty) < 0.5)
    vmax = float(np.nanpercentile(intensity[view], 99.7))
    fig, ax = plt.subplots(figsize=(7.1, 7.1), constrained_layout=True)
    ax.tripcolor(triang, facecolors=np.clip(intensity, 0.0, vmax), shading="flat", cmap="gray_r", vmin=0.0, vmax=vmax)
    draw_airfoil(ax)
    th = theory(field.alpha)
    colors = {"theory": "#333333", "su2": "#111111"}
    for branch in ("upper", "lower"):
        angle = float(th[f"{branch}_shock_abs"])
        length = 0.26
        ax.plot(
            [0.0, length], [0.0, length * math.tan(math.radians(angle))],
            color=colors["theory"], lw=1.8, ls="--",
            label="analytical shock" if branch == "upper" else None,
        )
        measured = float(metrics["shock_angles_abs_deg"][branch])
        ax.plot(
            [0.0, length], [0.0, length * math.tan(math.radians(measured))],
            color=colors["su2"], lw=1.5,
            label="SU2 near-nose ridge" if branch == "upper" else None,
        )
        oy = H if branch == "upper" else -H
        fan = th["fan"]
        for name in ("trailing", "leading"):
            a = float(fan[f"{branch}_{name}"])
            dx = 0.72
            ax.plot(
                [0.5, 0.5 + dx], [oy, oy + dx * math.tan(math.radians(a))],
                color="0.35", lw=1.25, ls="--",
                label="analytical fan edges" if branch == "upper" and name == "trailing" else None,
            )
    if field.alpha == 0.0:
        note = (
            fr"$\beta_{{theory}}={float(th['upper2']['beta_deg']):.3f}^\circ$; "
            fr"$\beta_{{SU2}}={float(metrics['shock_angles_beta_deg']['upper']):.3f}^\circ$"
        )
    else:
        note = (
            fr"upper: $\beta_{{th}}={float(th['upper2']['beta_deg']):.3f}^\circ$, "
            fr"$\beta_{{SU2}}={float(metrics['shock_angles_beta_deg']['upper']):.3f}^\circ$" "\n"
            fr"lower: $\beta_{{th}}={float(th['lower2']['beta_deg']):.3f}^\circ$, "
            fr"$\beta_{{SU2}}={float(metrics['shock_angles_beta_deg']['lower']):.3f}^\circ$"
        )
    ax.text(
        0.02, 0.965, note, transform=ax.transAxes, va="top", ha="left", fontsize=8.6,
        bbox=dict(facecolor="white", edgecolor="0.4", alpha=0.9, pad=4.0),
    )
    ax.set_xlim(-0.08, 1.25)
    ax.set_ylim(-0.70, 0.70)
    ax.set_xlabel(r"$x/c$")
    ax.set_ylabel(r"$y/c$")
    ax.set_title(fr"Native-mesh SU2 numerical Schlieren, $M_\infty=3$, $\alpha={field.alpha:.0f}^\circ$")
    ax.legend(loc="upper right", frameon=True)
    fig.savefig(out, dpi=330, bbox_inches="tight")
    plt.close(fig)


def plot_load_sweep(results: dict[str, object], out: Path) -> None:
    positive = sorted((float(k), v) for k, v in results.items())
    alpha = np.arange(-4, 5, dtype=float)
    cl_num = []
    cd_num = []
    for a in alpha:
        rec = dict(positive)[abs(float(a))]
        cl_num.append((-1.0 if a < 0.0 else 1.0) * rec["cl_mean"])
        cd_num.append(rec["cd_mean"])
    dense = np.linspace(-4.0, 4.0, 161)
    theory_cl = np.array([theory_loads(float(a))[0] for a in dense])
    theory_cd = np.array([theory_loads(float(a))[1] for a in dense])
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 5.0))
    axes[0].plot(dense, theory_cl, color="0.15", lw=2.4, label="Shock-expansion theory")
    axes[0].plot(alpha, cl_num, "o", mfc="white", mec="#1f77b4", mew=1.8, ms=6.5, label="SU2 HLLC/MUSCL")
    axes[1].plot(dense, theory_cd, color="0.15", lw=2.4)
    axes[1].plot(alpha, cd_num, "o", mfc="white", mec="#1f77b4", mew=1.8, ms=6.5)
    axes[0].set_title("(a) Lift", loc="left", fontweight="bold")
    axes[1].set_title("(b) Wave drag", loc="left", fontweight="bold")
    axes[0].set_ylabel(r"Lift coefficient, $C_L$")
    axes[1].set_ylabel(r"Pressure-drag coefficient, $C_{D,p}$")
    for ax in axes:
        ax.set_xlabel(r"Angle of attack, $\alpha$ (deg)")
        ax.grid(alpha=0.25)
        ax.set_xlim(-4.4, 4.4)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.subplots_adjust(top=0.83, bottom=0.17, left=0.07, right=0.98, wspace=0.16)
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.985), ncol=2, frameon=True)
    fig.savefig(out, dpi=280, bbox_inches="tight")
    plt.close(fig)


def interpolate_fields(
    field: FlowField,
    tri: np.ndarray,
    x_grid: np.ndarray,
    y_grid: np.ndarray,
) -> dict[str, np.ndarray]:
    """Interpolate native SU2 values to a display grid.

    Quantitative wave metrics remain native-grid calculations; this helper is
    restricted to common-display panels and the reproducible surrogate lesson.
    """
    xx, yy = np.meshgrid(x_grid, y_grid)
    triang = mtri.Triangulation(field.x, field.y, tri)
    result: dict[str, np.ndarray] = {}
    for name, values in {
        "rho": field.rho,
        "mach": field.mach,
        "pressure": field.pressure,
        "temperature": field.temperature,
        "speed": field.speed_ratio,
    }.items():
        interp = mtri.LinearTriInterpolator(triang, values)
        result[name] = np.asarray(interp(xx, yy).filled(np.nan), dtype=float)
    wall = np.where(xx <= 0.5, math.tan(EPS) * xx, math.tan(EPS) * (1.0 - xx))
    result["solid_mask"] = (xx >= 0.0) & (xx <= 1.0) & (np.abs(yy) <= wall)
    return result


def _fit_polynomial_cases(
    alpha: np.ndarray,
    stacked: np.ndarray,
    degree: int,
    target_alpha: float,
) -> np.ndarray:
    design = np.vander(alpha, N=degree + 1, increasing=True)
    coeff, *_ = np.linalg.lstsq(design, stacked.reshape(len(alpha), -1), rcond=None)
    target = np.asarray([target_alpha**power for power in range(degree + 1)])
    return (target @ coeff).reshape(stacked.shape[1:])


def build_reproducible_surrogate(
    fields: dict[int, FlowField],
    tri: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray], dict[str, np.ndarray], dict[str, object]]:
    x_grid = np.linspace(-0.30, 1.30, 256)
    y_grid = np.linspace(-0.66, 0.66, 112)
    gridded = {alpha: interpolate_fields(field, tri, x_grid, y_grid) for alpha, field in fields.items()}
    names = ("rho", "mach", "pressure", "temperature")
    common = np.ones_like(gridded[0]["rho"], dtype=bool)
    for record in gridded.values():
        common &= np.isfinite(record["rho"]) & ~record["solid_mask"]
    validation_scores: dict[str, float] = {}
    for degree in (1, 2):
        scores = []
        for name in names:
            training = np.stack([gridded[a][name] for a in (0, 1, 2)])
            pred = _fit_polynomial_cases(np.asarray([0.0, 1.0, 2.0]), training, degree, 3.0)
            truth = gridded[3][name]
            scale = float(np.nanmax(truth[common]) - np.nanmin(truth[common]))
            scores.append(float(np.sqrt(np.nanmean((pred[common] - truth[common]) ** 2)) / max(scale, 1e-12)))
        validation_scores[str(degree)] = float(np.mean(scores))
    degree = min((1, 2), key=lambda item: validation_scores[str(item)])
    target = {name: gridded[4][name] for name in names}
    prediction: dict[str, np.ndarray] = {}
    metrics: dict[str, object] = {
        "model": "pixelwise polynomial response surface",
        "training_alpha_deg": [0, 1, 2],
        "validation_alpha_deg": 3,
        "test_alpha_deg": 4,
        "selected_degree": degree,
        "validation_normalized_rmse_by_degree": validation_scores,
        "test": {},
    }
    for name in names:
        training = np.stack([gridded[a][name] for a in (0, 1, 2, 3)])
        pred = _fit_polynomial_cases(np.asarray([0.0, 1.0, 2.0, 3.0]), training, degree, 4.0)
        truth = target[name]
        scale = float(np.nanmax(truth[common]) - np.nanmin(truth[common]))
        pred = np.clip(pred, max(1e-6, float(np.nanmin(truth[common]) - 0.2 * scale)), float(np.nanmax(truth[common]) + 0.2 * scale))
        pred[~common] = np.nan
        prediction[name] = pred
        metrics["test"][name] = {
            "normalized_mae": float(np.nanmean(np.abs(pred[common] - truth[common])) / max(scale, 1e-12)),
            "normalized_rmse": float(np.sqrt(np.nanmean((pred[common] - truth[common]) ** 2)) / max(scale, 1e-12)),
        }
    return x_grid, y_grid, target, prediction, metrics


def plot_surrogate_workflow(out: Path) -> None:
    fig, ax = plt.subplots(figsize=(12.0, 5.0), constrained_layout=True)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 5)
    ax.axis("off")
    boxes = [
        (0.25, 2.9, 2.0, 1.15, "SU2 v8.5\n$\\alpha=0^\\circ$–$4^\\circ$"),
        (2.75, 2.9, 2.0, 1.15, "Native restart\n$\\rho, u, v, p$"),
        (5.25, 2.9, 2.0, 1.15, "Common display grid\n112 × 256"),
        (7.75, 2.9, 2.0, 1.15, "Validation at $3^\\circ$\nselect degree"),
        (5.25, 0.75, 2.0, 1.15, "Fit response surface\n$0^\\circ$–$3^\\circ$"),
        (8.65, 0.75, 2.55, 1.15, "Held-out $4^\\circ$\nprediction + error"),
    ]
    for x, y, w, h, label in boxes:
        patch = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.05", facecolor="#eaf2f8", edgecolor="#1f4e79", linewidth=1.6)
        ax.add_patch(patch)
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center", fontsize=11)
    arrows = [((2.25, 3.48), (2.75, 3.48)), ((4.75, 3.48), (5.25, 3.48)), ((7.25, 3.48), (7.75, 3.48)), ((8.75, 2.9), (6.25, 1.9)), ((7.25, 1.32), (8.65, 1.32))]
    for start, end in arrows:
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=13, linewidth=1.5, color="#1f4e79"))
    ax.text(6.0, 4.65, "SU2-only reproducible field-surrogate workflow", ha="center", va="center", fontsize=15, fontweight="bold")
    ax.text(6.0, 0.18, "All CFD targets and inputs are generated by the supplied SU2 cases; interpolation is display-only.", ha="center", fontsize=10, color="0.25")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_surrogate_comparison(
    x_grid: np.ndarray,
    y_grid: np.ndarray,
    target: dict[str, np.ndarray],
    prediction: dict[str, np.ndarray],
    metrics: dict[str, object],
    out: Path,
) -> None:
    names = ("rho", "mach", "pressure", "temperature")
    labels = (r"$\rho/\rho_\infty$", r"$M$", r"$p/p_\infty$", r"$T/T_\infty$")
    extent = [float(x_grid[0]), float(x_grid[-1]), float(y_grid[0]), float(y_grid[-1])]
    fig, axes = plt.subplots(4, 3, figsize=(10.7, 10.25), constrained_layout=True)
    for row, (name, label) in enumerate(zip(names, labels)):
        truth = target[name]
        pred = prediction[name]
        valid = np.isfinite(truth) & np.isfinite(pred)
        lo, hi = np.nanpercentile(truth[valid], [0.4, 99.6])
        scale = max(float(np.nanmax(truth[valid]) - np.nanmin(truth[valid])), 1e-12)
        error = 100.0 * np.abs(pred - truth) / (np.abs(truth) + 0.05 * scale)
        emax = float(np.nanpercentile(error[valid], 99.0))
        for col, data in enumerate((truth, pred, error)):
            cmap = "turbo" if col < 2 else "magma"
            vmin, vmax = (lo, hi) if col < 2 else (0.0, emax)
            im = axes[row, col].imshow(data, origin="lower", extent=extent, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)
            draw_airfoil(axes[row, col])
            axes[row, col].set_xlim(extent[0], extent[1])
            axes[row, col].set_ylim(extent[2], extent[3])
            axes[row, col].set_xlabel(r"$x/c$")
            axes[row, col].set_ylabel(r"$y/c$")
            fig.colorbar(im, ax=axes[row, col], shrink=0.82, pad=0.02)
        rec = metrics["test"][name]
        axes[row, 0].set_title(f"{label}: SU2 test field", fontsize=9.5)
        axes[row, 1].set_title(f"{label}: surrogate", fontsize=9.5)
        axes[row, 2].set_title(
            f"{label}: local error (%)\nNRMSE={100.0 * float(rec['normalized_rmse']):.2f}%",
            fontsize=9.2,
        )
    fig.suptitle(
        fr"Reproducible held-out case: SU2 $M_\infty=3$, $\alpha=4^\circ$; polynomial degree {metrics['selected_degree']}",
        fontsize=13,
    )
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)


def _render_common_panel(
    field: FlowField,
    tri: np.ndarray,
    variable: str,
    size: tuple[int, int],
    vmin: float,
    vmax: float,
) -> Image.Image:
    width, height = size
    x_grid = np.linspace(-0.2, 1.28, width)
    y_grid = np.linspace(0.65, -0.65, height)
    record = interpolate_fields(field, tri, x_grid, y_grid)
    values = record[variable]
    cmap = plt.get_cmap("viridis" if variable == "speed" else "turbo")
    boundaries = np.linspace(vmin, vmax, 11)
    norm = BoundaryNorm(boundaries, cmap.N, clip=True)
    rgba = (255.0 * cmap(norm(values))).astype(np.uint8)
    rgba[~np.isfinite(values)] = np.asarray([255, 255, 255, 255], dtype=np.uint8)
    panel = Image.fromarray(rgba, mode="RGBA")
    overlay = Image.new("RGBA", panel.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    def px(x: float) -> float:
        return (x + 0.2) / 1.48 * (width - 1)
    def py(y: float) -> float:
        return (0.65 - y) / 1.30 * (height - 1)
    for x in (0.0, 0.5, 1.0):
        draw.line([(px(x), 0), (px(x), height)], fill=(220, 220, 220, 78), width=2)
    for y in (-0.4, 0.0, 0.4):
        draw.line([(0, py(y)), (width, py(y))], fill=(220, 220, 220, 78), width=2)
    poly = [(px(0.0), py(0.0)), (px(0.5), py(H)), (px(1.0), py(0.0)), (px(0.5), py(-H))]
    draw.polygon(poly, fill=(255, 255, 255, 255), outline=(20, 20, 20, 255), width=max(2, width // 350))
    return Image.alpha_composite(panel, overlay).convert("RGB")


def rebuild_common_comparison(
    source: Path,
    destination: Path,
    field: FlowField,
    tri: np.ndarray,
    top_variable: str,
    bottom_variable: str,
    top_range: tuple[float, float],
    bottom_range: tuple[float, float],
) -> None:
    base = Image.open(source).convert("RGB")
    width, height = base.size
    x0, x1 = round(0.2780 * width), round(0.4715 * width)
    top_y0, top_y1 = round(0.1012 * height), round(0.4785 * height)
    bot_y0, bot_y1 = round(0.5323 * height), round(0.9095 * height)
    top = _render_common_panel(field, tri, top_variable, (x1 - x0, top_y1 - top_y0), *top_range)
    bottom = _render_common_panel(field, tri, bottom_variable, (x1 - x0, bot_y1 - bot_y0), *bottom_range)
    base.paste(top, (x0, top_y0))
    base.paste(bottom, (x0, bot_y0))
    base.save(destination, quality=95, subsampling=0)


def build_case_metrics(field: FlowField, shock_metrics_path: Path) -> dict[str, object]:
    th = theory(field.alpha)
    with shock_metrics_path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    shock_angles = raw.get("shock_angles_deg", {})
    if field.alpha == 0.0:
        upper_abs = float(shock_angles["upper"])
        lower_abs = -upper_abs
    else:
        upper_abs = float(shock_angles["upper"])
        lower_abs = float(shock_angles["lower"])
    upper2 = sample_state(field, "upper", "postshock", upper_abs)
    lower2 = sample_state(field, "lower", "postshock", lower_abs)
    fan_theory = th["fan"]
    upper3 = sample_state(field, "upper", "expanded", float(fan_theory["upper_trailing"]))
    lower3 = sample_state(field, "lower", "expanded", float(fan_theory["lower_trailing"]))
    upper_fan = extract_fan_edges(
        field, "upper", float(upper2["p_ratio"]), float(upper3["p_ratio"]),
        float(fan_theory["upper_leading"]), float(fan_theory["upper_trailing"]),
    )
    lower_fan = extract_fan_edges(
        field, "lower", float(lower2["p_ratio"]), float(lower3["p_ratio"]),
        float(fan_theory["lower_leading"]), float(fan_theory["lower_trailing"]),
    )
    return {
        "case": field.case,
        "alpha_deg": field.alpha,
        "shock_angles_abs_deg": {"upper": upper_abs, "lower": lower_abs},
        "shock_angles_beta_deg": {
            "upper": upper_abs - field.alpha,
            "lower": field.alpha - lower_abs,
        },
        "plateau_states": {"upper2": upper2, "lower2": lower2, "upper3": upper3, "lower3": lower3},
        "fan_edges_deg": {"upper": upper_fan, "lower": lower_fan},
        "theory": th,
    }


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    source_media = args.source_media
    if source_media is None:
        packaged_templates = repo / "report_templates"
        if packaged_templates.is_dir():
            source_media = packaged_templates
    mesh = repo / "meshes/diamond_euler_sharp_medium_720x181.su2"
    tri = read_mesh_triangles(mesh)
    all_metrics: dict[str, object] = {"solver": "SU2 v8.5.0", "scheme": "HLLC startup; MUSCL/Venkatakrishnan production"}
    fields: dict[int, FlowField] = {}
    for alpha in range(5):
        case = f"euler_alpha{alpha}"
        case_dir = repo / "cases" / case
        field = load_restart(case_dir / "restart_second_order.csv", case, float(alpha))
        fields[alpha] = field
        if alpha in (0, 4):
            metrics = build_case_metrics(field, case_dir / "case_metrics.json")
            metrics["history"] = read_history_mean(case_dir / "history_second_order.csv")
            all_metrics[case] = metrics
            with (out / f"euler_alpha{alpha}_metrics.json").open("w", encoding="utf-8") as handle:
                json.dump(metrics, handle, indent=2, sort_keys=True)
            plot_four_fields(field, tri, out / f"Figure_SU2_Euler_alpha{alpha}_fields.png")
            plot_schlieren(field, tri, metrics, out / f"Figure_SU2_Euler_alpha{alpha}_schlieren.png")
    sweep: dict[str, object] = {}
    for alpha in range(5):
        case_dir = repo / "cases" / f"euler_alpha{alpha}"
        sweep[str(float(alpha))] = read_history_mean(case_dir / "history_second_order.csv")
    all_metrics["load_sweep"] = sweep
    plot_load_sweep(sweep, out / "Figure_SU2_Euler_load_sweep.png")
    x_grid, y_grid, target, prediction, surrogate_metrics = build_reproducible_surrogate(fields, tri)
    all_metrics["surrogate"] = surrogate_metrics
    plot_surrogate_workflow(out / "Figure_SU2_surrogate_workflow.png")
    plot_surrogate_comparison(
        x_grid, y_grid, target, prediction, surrogate_metrics,
        out / "Figure_SU2_surrogate_alpha4.png",
    )
    if source_media:
        source_media = source_media.resolve()
        rebuild_common_comparison(
            source_media / "image19.jpg", out / "Figure_SU2_common_alpha0_density_temperature.jpg",
            fields[0], tri, "rho", "temperature", (0.2, 3.141), (0.62, 2.78),
        )
        rebuild_common_comparison(
            source_media / "image20.jpg", out / "Figure_SU2_common_alpha0_mach_speed.jpg",
            fields[0], tri, "mach", "speed", (0.098, 4.005), (0.85, 1.0911),
        )
        rebuild_common_comparison(
            source_media / "image21.jpg", out / "Figure_SU2_common_alpha4_density_temperature.jpg",
            fields[4], tri, "rho", "temperature", (0.2, 3.141), (0.62, 2.801),
        )
        rebuild_common_comparison(
            source_media / "image22.jpg", out / "Figure_SU2_common_alpha4_mach_speed.jpg",
            fields[4], tri, "mach", "speed", (0.095, 4.005), (0.85, 1.0911),
        )
    with (out / "su2_report_metrics.json").open("w", encoding="utf-8") as handle:
        json.dump(all_metrics, handle, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
