#!/usr/bin/env python3
"""Audit MFC binary fields and render cropped fixed-scale research movies."""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import sys
from pathlib import Path

import numpy as np


parser = argparse.ArgumentParser()
parser.add_argument("--case-dir", type=Path, required=True)
parser.add_argument("--mfc-root", type=Path, required=True)
parser.add_argument("--dt", type=float, required=True)
parser.add_argument("--label", required=True)
parser.add_argument("--fps", type=float, default=12.0)
parser.add_argument("--xlim", type=float, nargs=2, default=(-1.25, 4.75))
parser.add_argument("--ylim", type=float, nargs=2, default=(-1.25, 4.25))
parser.add_argument("--audit-only", action="store_true")
args = parser.parse_args()

if args.dt <= 0.0 or args.fps <= 0.0:
    parser.error("--dt and --fps must be positive")

sys.path.insert(0, str(args.mfc_root / "toolchain"))
from mfc.viz.reader import assemble, discover_timesteps  # noqa: E402

case_dir = args.case_dir.resolve()
product_dir = case_dir / "movie_products"
product_dir.mkdir(parents=True, exist_ok=True)
steps = discover_timesteps(str(case_dir), "binary")
if not steps:
    raise SystemExit(f"ERROR: no binary post-process timesteps under {case_dir}")

required = ("rho", "pres", "vel1", "vel2", "omega3")
audit_steps = steps[-2:] if len(steps) >= 2 else steps
audit_rows: list[dict[str, float | int | bool]] = []
audit_ok = True
fluid_mask_global: np.ndarray | None = None
fluid_mask_source: str | None = None

AIRFOIL_X = np.array([0.0, 0.5, 1.0, 0.5, 0.0])
AIRFOIL_Y = np.array([0.0, 0.0702704174, 0.0, -0.0702704174, 0.0])
AIRFOIL_HALF_HEIGHT = 0.0702704174


def geometry_fluid_mask(x_cc: np.ndarray, y_cc: np.ndarray) -> np.ndarray:
    """Mask the exact diamond plus a three-cell immersed-boundary guard band."""
    dx = float(np.min(np.diff(x_cc)))
    dy = float(np.min(np.diff(y_cc)))
    pad = 3.0 * max(dx, dy)
    xx = x_cc[:, None]
    yy = y_cc[None, :]
    chord_band = (xx >= -pad) & (xx <= 1.0 + pad)
    clipped_x = np.clip(xx, 0.0, 1.0)
    half_height = AIRFOIL_HALF_HEIGHT * (
        1.0 - np.abs(2.0 * clipped_x - 1.0)
    )
    solid_or_guard = chord_band & (np.abs(yy) <= half_height + pad)
    return ~solid_or_guard


def resolve_fluid_mask(assembled) -> tuple[np.ndarray, str]:
    if "ib_markers" in assembled.variables:
        return assembled.variables["ib_markers"] == 0, "ib_markers"
    return (
        geometry_fluid_mask(assembled.x_cc, assembled.y_cc),
        "diamond_geometry_plus_three_cell_guard",
    )


def movie_field(assembled, variable: str) -> np.ndarray:
    if variable == "omega3":
        return assembled.variables["omega3"]
    if variable == "schlieren":
        rho = assembled.variables["rho"]
        dx = float(np.mean(np.diff(assembled.x_cc)))
        dy = float(np.mean(np.diff(assembled.y_cc)))
        drho_dx, drho_dy = np.gradient(rho, dx, dy, edge_order=2)
        return np.hypot(drho_dx, drho_dy)
    raise KeyError(variable)


for step in audit_steps:
    assembled = assemble(str(case_dir), step, fmt="binary")
    missing = sorted(set(required) - set(assembled.variables))
    if missing:
        raise SystemExit(f"ERROR: step {step} lacks required variables: {missing}")
    rho = assembled.variables["rho"]
    pres = assembled.variables["pres"]
    vel1 = assembled.variables["vel1"]
    vel2 = assembled.variables["vel2"]
    fluid, this_mask_source = resolve_fluid_mask(assembled)
    if not fluid.any():
        raise SystemExit(f"ERROR: step {step} contains no fluid cells")
    if fluid_mask_global is None:
        fluid_mask_global = fluid.copy()
        fluid_mask_source = this_mask_source
    elif not np.array_equal(fluid_mask_global, fluid):
        raise SystemExit("ERROR: fixed-airfoil fluid mask changed between audit steps")
    elif fluid_mask_source != this_mask_source:
        raise SystemExit("ERROR: fluid-mask source changed between audit steps")
    finite = all(np.isfinite(assembled.variables[name][fluid]).all() for name in required)
    positive = bool(np.nanmin(rho[fluid]) > 0.0 and np.nanmin(pres[fluid]) > 0.0)
    dx = float(np.min(np.diff(assembled.x_cc)))
    dy = float(np.min(np.diff(assembled.y_cc)))
    soundspeed = np.sqrt(1.4 * pres[fluid] / rho[fluid])
    cfl_proxy = args.dt * np.nanmax(
        (np.abs(vel1[fluid]) + soundspeed) / dx
        + (np.abs(vel2[fluid]) + soundspeed) / dy
    )
    row = {
        "step": step,
        "time": step * args.dt,
        "finite": bool(finite),
        "rho_min": float(np.nanmin(rho[fluid])),
        "rho_max": float(np.nanmax(rho[fluid])),
        "pres_min": float(np.nanmin(pres[fluid])),
        "pres_max": float(np.nanmax(pres[fluid])),
        "speed_max": float(np.nanmax(np.hypot(vel1[fluid], vel2[fluid]))),
        "omega_abs_max": float(np.nanmax(np.abs(assembled.variables["omega3"][fluid]))),
        "cfl_proxy": float(cfl_proxy),
    }
    row_ok = bool(finite and positive and math.isfinite(cfl_proxy) and cfl_proxy < 1.0)
    row["pass"] = row_ok
    audit_ok = audit_ok and row_ok
    audit_rows.append(row)
    del assembled, rho, pres, vel1, vel2, soundspeed
    gc.collect()

audit_path = product_dir / f"{args.label}-field-audit.json"
audit_path.write_text(
    json.dumps(
        {
            "label": args.label,
            "case_dir": str(case_dir),
            "dt": args.dt,
            "available_steps": steps,
            "audit_steps": audit_rows,
            "fluid_mask_source": fluid_mask_source,
            "schlieren_source": "derived_density_gradient_magnitude",
            "pass": audit_ok,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
if not audit_ok:
    raise SystemExit(f"ERROR: field-health audit failed; see {audit_path}")
if args.audit_only:
    print(f"AUDIT=PASS {audit_path}")
    raise SystemExit(0)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import imageio.v2 as imageio  # noqa: E402

if fluid_mask_global is None:
    raise SystemExit("ERROR: immersed-boundary fluid mask was not initialized")


def crop_and_stride(assembled, data: np.ndarray):
    xmask = (assembled.x_cc >= args.xlim[0]) & (assembled.x_cc <= args.xlim[1])
    ymask = (assembled.y_cc >= args.ylim[0]) & (assembled.y_cc <= args.ylim[1])
    if not xmask.any() or not ymask.any():
        raise ValueError("movie crop does not overlap the MFC grid")
    x_idx = np.flatnonzero(xmask)
    y_idx = np.flatnonzero(ymask)
    target_cells = 1_000_000
    stride = max(1, math.ceil(math.sqrt((x_idx.size * y_idx.size) / target_cells)))
    x_idx = x_idx[::stride]
    y_idx = y_idx[::stride]
    cropped = data[np.ix_(x_idx, y_idx)]
    fluid_crop = fluid_mask_global[np.ix_(x_idx, y_idx)]
    return assembled.x_cc[x_idx], assembled.y_cc[y_idx], np.where(fluid_crop, cropped, np.nan)


sample_indices = np.unique(np.linspace(0, len(steps) - 1, min(9, len(steps)), dtype=int))
sample_steps = [steps[int(i)] for i in sample_indices]
scales: dict[str, tuple[float, float]] = {}
for variable in ("omega3", "schlieren"):
    samples: list[np.ndarray] = []
    for step in sample_steps:
        source_variable = variable if variable == "omega3" else "rho"
        assembled = assemble(str(case_dir), step, fmt="binary", var=source_variable)
        field = movie_field(assembled, variable)
        _, _, cropped = crop_and_stride(assembled, field)
        finite = cropped[np.isfinite(cropped)]
        if finite.size:
            samples.append(finite[:: max(1, finite.size // 250_000)])
        del assembled, field, cropped, finite
        gc.collect()
    if not samples:
        raise SystemExit(f"ERROR: no finite {variable} samples")
    values = np.concatenate(samples)
    if variable == "omega3":
        limit = float(np.quantile(np.abs(values), 0.995))
        if not math.isfinite(limit) or limit <= 0.0:
            limit = float(np.max(np.abs(values)))
        scales[variable] = (-limit, limit)
    else:
        lo, hi = np.quantile(values, (0.002, 0.998))
        if not math.isfinite(float(lo)) or not math.isfinite(float(hi)) or lo >= hi:
            lo, hi = np.min(values), np.max(values)
        scales[variable] = (float(lo), float(hi))
    del values, samples
    gc.collect()

airfoil_x = AIRFOIL_X
airfoil_y = AIRFOIL_Y
movie_specs = {
    "omega3": ("RdBu_r", r"$\omega_z c/U_\infty$", "vorticity-shedding"),
    "schlieren": (
        "gray",
        r"$|\nabla \rho|\,c/\rho_\infty$",
        "shock-formation",
    ),
}
movie_paths: dict[str, str] = {}

for variable, (cmap, colorbar_label, suffix) in movie_specs.items():
    vmin, vmax = scales[variable]
    movie_path = product_dir / f"{args.label}-{suffix}.mp4"
    with imageio.get_writer(
        movie_path,
        fps=args.fps,
        codec="libx264",
        quality=8,
        macro_block_size=2,
        ffmpeg_log_level="error",
    ) as writer:
        for frame_index, step in enumerate(steps):
            source_variable = variable if variable == "omega3" else "rho"
            assembled = assemble(
                str(case_dir), step, fmt="binary", var=source_variable
            )
            field = movie_field(assembled, variable)
            x, y, data = crop_and_stride(assembled, field)
            fig, ax = plt.subplots(figsize=(10.0, 8.0), dpi=120)
            pcm = ax.pcolormesh(
                x,
                y,
                data.T,
                shading="auto",
                cmap=cmap,
                vmin=vmin,
                vmax=vmax,
                rasterized=True,
            )
            ax.fill(airfoil_x, airfoil_y, color="black", zorder=4)
            ax.plot(airfoil_x, airfoil_y, color="white", linewidth=0.45, zorder=5)
            ax.set_xlim(*args.xlim)
            ax.set_ylim(*args.ylim)
            ax.set_aspect("equal", adjustable="box")
            ax.set_xlabel(r"$x/c$")
            ax.set_ylabel(r"$y/c$")
            ax.set_title(
                f"MFC viscous/no-model, Mach 3, AoA 40° — {suffix.replace('-', ' ')}\n"
                f"t = {step * args.dt:.4f}"
            )
            cbar = fig.colorbar(pcm, ax=ax, pad=0.02, shrink=0.90)
            cbar.set_label(colorbar_label)
            fig.tight_layout()
            fig.canvas.draw()
            rgba = np.asarray(fig.canvas.buffer_rgba())
            writer.append_data(rgba[:, :, :3])
            if frame_index in (0, len(steps) - 1):
                keyframe = product_dir / f"{args.label}-{suffix}-step-{step}.png"
                fig.savefig(keyframe, dpi=160)
            plt.close(fig)
            del assembled, field, x, y, data, rgba
            gc.collect()
    movie_paths[variable] = str(movie_path)

manifest = {
    "label": args.label,
    "case_dir": str(case_dir),
    "dt": args.dt,
    "steps": steps,
    "times": [step * args.dt for step in steps],
    "fixed_scales": {key: list(value) for key, value in scales.items()},
    "crop": {"xlim": args.xlim, "ylim": args.ylim},
    "fps": args.fps,
    "movies": movie_paths,
    "audit": str(audit_path),
    "fluid_mask_source": fluid_mask_source,
    "field_sources": {
        "omega3": "MFC post_process omega3",
        "schlieren": "derived magnitude of density gradient",
    },
}
manifest_path = product_dir / f"{args.label}-movie-manifest.json"
manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print(f"AUDIT=PASS {audit_path}")
print(f"VORTICITY_MOVIE={movie_paths['omega3']}")
print(f"SCHLIEREN_MOVIE={movie_paths['schlieren']}")
print(f"MOVIE_MANIFEST={manifest_path}")
