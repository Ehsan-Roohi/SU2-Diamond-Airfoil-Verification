#!/usr/bin/env python3
"""Build the email-ready Reynolds figure from already completed outputs.

This is a plotting-only post-processing step. It does not read MFC restart
fields and does not rerun the control-volume force reconstruction.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import tarfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image


DIRECT_CASES = ("re1e4_f180", "re5e4_f180", "re1e5_f180")
DIRECT_REYNOLDS = np.asarray((1.0e4, 5.0e4, 1.0e5))
DIRECT_RE_LABELS = (r"$10^4$", r"$5\times10^4$", r"$10^5$")
CONTEXT_FORCE_CASE = "re1e6_f270_mature"
IMAGE_SPECS = (
    ("re1e4_f180", 21600, 6.0, r"$Re_c=10^4$", "f180; high-viscosity control"),
    ("re5e4_f180", 21600, 6.0, r"$Re_c=5\times10^4$", "f180"),
    ("re1e5_f180", 21600, 6.0, r"$Re_c=10^5$", "f180"),
    ("re1e6_retained", 167400, 31.0, r"$Re_c=10^6$", "f270; later-time context"),
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def force_rows(force_output: Path) -> dict[str, dict[str, str]]:
    required = set(DIRECT_CASES) | {CONTEXT_FORCE_CASE}
    rows = read_csv(force_output / "control_volume_force_summary.csv")
    selected = {
        row["case"]: row
        for row in rows
        if row["case"] in required and row["control_volume"] == "nominal"
    }
    missing = sorted(required - set(selected))
    if missing:
        raise RuntimeError(f"missing nominal force summaries: {missing}")
    return selected


def flow_rows(flow_output: Path) -> dict[str, dict[str, dict[str, str]]]:
    rows = read_csv(flow_output / "tim_colonius_same_grid_reynolds_summary.csv")
    selected: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        if row["case"] in DIRECT_CASES and row["window"] == "post_startup_t3_t6":
            selected.setdefault(row["metric"], {})[row["case"]] = row
    required = (
        "wake_enstrophy",
        "wake_abs_vorticity_p99",
        "wake_pressure_rms",
        "shock_standoff_over_c",
    )
    for metric in required:
        missing = sorted(set(DIRECT_CASES) - set(selected.get(metric, {})))
        if missing:
            raise RuntimeError(f"missing {metric} summaries: {missing}")
    return selected


def open_snapshot(source: Path, case: str, step: int) -> Image.Image:
    """Open a vorticity raster and restore conventional Cartesian +y-up view.

    The ML PNG writer transposes (x,y) fields into image (row,column) storage
    without reversing y. PNG row zero is displayed at the top, so the saved
    raster is vertically inverted relative to a Cartesian plot. The flip here
    changes presentation only; values and vorticity signs are untouched.
    """

    name = f"images/vorticity/{case}_s{step:09d}.png"
    if source.is_dir():
        path = source / name
        if not path.is_file():
            raise FileNotFoundError(path)
        with Image.open(path) as image:
            return image.convert("RGB").transpose(Image.Transpose.FLIP_TOP_BOTTOM)

    if not source.is_file():
        raise FileNotFoundError(source)
    with tarfile.open(source, "r:gz") as archive:
        member_name = next(
            (candidate for candidate in (name, f"./{name}") if candidate in archive.getnames()),
            None,
        )
        if member_name is None:
            raise FileNotFoundError(f"{name} not found in {source}")
        payload = archive.extractfile(member_name)
        if payload is None:
            raise RuntimeError(f"could not read {member_name} from {source}")
        with Image.open(io.BytesIO(payload.read())) as image:
            return image.convert("RGB").transpose(Image.Transpose.FLIP_TOP_BOTTOM)


def change_and_variability(
    rows: dict[str, dict[str, str]], mean_key: str, std_key: str
) -> tuple[list[float], list[float]]:
    base_mean = float(rows[DIRECT_CASES[0]][mean_key])
    base_std = float(rows[DIRECT_CASES[0]][std_key])
    change: list[float] = []
    spread: list[float] = []
    for index, case in enumerate(DIRECT_CASES):
        mean = float(rows[case][mean_key])
        std = float(rows[case][std_key])
        change.append(100.0 * (mean / base_mean - 1.0))
        spread.append(
            0.0
            if index == 0
            else 100.0 * math.hypot(std, base_std) / abs(base_mean)
        )
    return change, spread


def configure_relative_axis(
    ax: plt.Axes, ylabel: str, ylim: tuple[float, float]
) -> None:
    ax.axhline(0.0, color="#6b7280", linewidth=0.9, linestyle="--", zorder=0)
    ax.set_xticks(range(3), DIRECT_RE_LABELS)
    ax.set_xlabel(r"Chord Reynolds number, $Re_c$")
    ax.set_ylabel(ylabel)
    ax.set_ylim(*ylim)
    ax.grid(axis="y", color="#d1d5db", linewidth=0.7, alpha=0.7)
    ax.spines[["top", "right"]].set_visible(False)


def plot_relative_series(
    ax: plt.Axes,
    values: list[float],
    errors: list[float],
    label: str,
    color: str,
    marker: str,
) -> None:
    ax.errorbar(
        range(3),
        values,
        yerr=errors,
        color=color,
        marker=marker,
        markersize=6.5,
        linewidth=2.0,
        capsize=3.5,
        capthick=1.2,
        label=label,
        zorder=3,
    )


def build(args: argparse.Namespace) -> None:
    force = force_rows(args.force_output)
    flow = flow_rows(args.flow_output)
    images = [
        open_snapshot(args.image_source, case, step)
        for case, step, _time, _title, _note in IMAGE_SPECS
    ]
    args.output.mkdir(parents=True, exist_ok=True)

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9.5,
            "axes.labelsize": 9.5,
            "axes.titlesize": 10.5,
            "legend.fontsize": 8.6,
            "pdf.fonttype": 42,
        }
    )
    fig = plt.figure(figsize=(16.0, 8.8), facecolor="white")
    grid = fig.add_gridspec(
        2,
        4,
        height_ratios=(1.18, 0.92),
        left=0.052,
        right=0.985,
        bottom=0.155,
        top=0.865,
        wspace=0.31,
        hspace=0.30,
    )

    fig.suptitle(
        r"Mach 3 flow over a $40^{\circ}$ diamond airfoil",
        x=0.052,
        y=0.965,
        ha="left",
        fontsize=19,
        fontweight="bold",
        color="#111827",
    )
    fig.text(
        0.052,
        0.916,
        "All Reynolds cases shown in Cartesian orientation (+y upward); direct statistics use the common f180 grid",
        ha="left",
        fontsize=12.3,
        color="#374151",
    )

    for index, (image, spec) in enumerate(zip(images, IMAGE_SPECS)):
        _case, _step, time, title, note = spec
        ax = fig.add_subplot(grid[0, index])
        ax.imshow(image)
        ax.set_axis_off()
        ax.set_title(f"({chr(97 + index)})  {title}", pad=7, fontweight="bold")
        ax.text(
            0.018,
            0.026,
            rf"$t={time:g}$; {note}",
            transform=ax.transAxes,
            fontsize=8.2,
            color="#111827",
            bbox={
                "boxstyle": "round,pad=0.25",
                "facecolor": "white",
                "alpha": 0.84,
                "edgecolor": "none",
            },
        )
        if index == 0:
            ax.annotate(
                r"$+y$",
                xy=(0.08, 0.91),
                xytext=(0.08, 0.70),
                xycoords="axes fraction",
                textcoords="axes fraction",
                ha="center",
                fontsize=9,
                arrowprops={"arrowstyle": "-|>", "color": "#111827", "lw": 1.2},
            )

    # Absolute coefficients remove the ambiguity of plotting zero at the
    # Re=1e4 reference. The direct same-grid sequence is connected; Re=1e6 is
    # intentionally isolated because its grid and averaging window differ.
    ax = fig.add_subplot(grid[1, 0:2])
    cl_mean = [float(force[case]["CL_mean"]) for case in DIRECT_CASES]
    cl_std = [float(force[case]["CL_temporal_std"]) for case in DIRECT_CASES]
    cd_mean = [float(force[case]["CD_mean"]) for case in DIRECT_CASES]
    cd_std = [float(force[case]["CD_temporal_std"]) for case in DIRECT_CASES]
    ax.errorbar(
        DIRECT_REYNOLDS,
        cl_mean,
        yerr=cl_std,
        color="#2563eb",
        marker="o",
        linewidth=2.2,
        capsize=3.5,
        label=r"$C_L$: f180, $t\in[3,6]$",
    )
    ax.errorbar(
        DIRECT_REYNOLDS,
        cd_mean,
        yerr=cd_std,
        color="#ea580c",
        marker="s",
        linewidth=2.2,
        capsize=3.5,
        label=r"$C_D$: f180, $t\in[3,6]$",
    )
    context = force[CONTEXT_FORCE_CASE]
    context_x = 1.0e6
    ax.errorbar(
        [context_x],
        [float(context["CL_mean"])],
        yerr=[float(context["CL_temporal_std"])],
        color="#2563eb",
        marker="D",
        markerfacecolor="white",
        markeredgewidth=1.6,
        markersize=7,
        capsize=3.5,
        linestyle="none",
    )
    ax.errorbar(
        [context_x],
        [float(context["CD_mean"])],
        yerr=[float(context["CD_temporal_std"])],
        color="#ea580c",
        marker="D",
        markerfacecolor="white",
        markeredgewidth=1.6,
        markersize=7,
        capsize=3.5,
        linestyle="none",
    )
    ax.set_xscale("log")
    ax.set_xlim(8.3e3, 1.25e6)
    ax.set_ylim(0.81, 0.905)
    ax.set_xticks(
        (1.0e4, 5.0e4, 1.0e5, 1.0e6),
        (r"$10^4$", r"$5\times10^4$", r"$10^5$", r"$10^6$"),
    )
    ax.set_xlabel(r"Chord Reynolds number, $Re_c$")
    ax.set_ylabel("Absolute reconstructed coefficient")
    ax.grid(axis="y", color="#d1d5db", linewidth=0.7, alpha=0.7)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="lower right", frameon=False)
    ax.set_title("(e) Absolute lift and drag coefficients", loc="left", fontweight="bold")
    ax.text(
        0.985,
        0.94,
        r"Open diamonds: f270, $t\in[26,31]$ (context only)",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=8.5,
        color="#4b5563",
    )
    ax.annotate(
        f"$C_L={cl_mean[0]:.3f}$\n$C_D={cd_mean[0]:.3f}$",
        (DIRECT_REYNOLDS[0], 0.5 * (cl_mean[0] + cd_mean[0])),
        xytext=(25, 0),
        textcoords="offset points",
        fontsize=8.5,
        va="center",
        color="#374151",
    )

    wake_enstrophy = flow["wake_enstrophy"]
    wake_p99 = flow["wake_abs_vorticity_p99"]
    ens, ens_sd = change_and_variability(wake_enstrophy, "mean", "temporal_std")
    p99, p99_sd = change_and_variability(wake_p99, "mean", "temporal_std")
    ax = fig.add_subplot(grid[1, 2])
    configure_relative_axis(ax, r"Change from $Re_c=10^4$ (%)", (-22.0, 55.0))
    plot_relative_series(ax, ens, ens_sd, "Wake enstrophy", "#9333ea", "o")
    plot_relative_series(ax, p99, p99_sd, r"Wake $|\omega_z|_{99}$", "#0891b2", "s")
    ax.legend(loc="upper right", frameon=False)
    ax.set_title("(f) Wake dynamics (fixed grid)", loc="left", fontweight="bold")

    shock_rows = flow["shock_standoff_over_c"]
    pressure_rows = flow["wake_pressure_rms"]
    shock, shock_sd = change_and_variability(shock_rows, "mean", "temporal_std")
    pressure, pressure_sd = change_and_variability(pressure_rows, "mean", "temporal_std")
    ax = fig.add_subplot(grid[1, 3])
    configure_relative_axis(ax, r"Change from $Re_c=10^4$ (%)", (-3.2, 3.2))
    plot_relative_series(ax, shock, shock_sd, "Shock standoff", "#4b5563", "o")
    plot_relative_series(ax, pressure, pressure_sd, "Wake pressure RMS", "#16a34a", "s")
    ax.legend(loc="upper left", frameon=False)
    ax.set_title("(g) Shock / pressure (fixed grid)", loc="left", fontweight="bold")

    footer = (
        r"Vorticity snapshots use one fixed raster scale, $\omega_z\in[-17,17]$. "
        r"Whiskers: temporal SD (relative panels use quadrature SD; not confidence intervals). "
        "Loads are control-volume momentum-balance reconstructions because native IB forces are NaN. "
        "The ML-storage PNGs were flipped vertically here only to restore Cartesian +y-up presentation."
    )
    fig.text(0.052, 0.055, footer, ha="left", va="bottom", fontsize=8.4, color="#4b5563", wrap=True)
    fig.text(
        0.985,
        0.055,
        "MFC / iLES",
        ha="right",
        va="bottom",
        fontsize=9,
        fontweight="bold",
        color="#6b7280",
    )

    png = args.output / "TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.png"
    pdf = args.output / "TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.pdf"
    fig.savefig(png, dpi=300, facecolor="white")
    fig.savefig(pdf, facecolor="white")
    plt.close(fig)

    readme = args.output / "READ_ME_FIRST.md"
    readme.write_text(
        """# Tim Colonius - corrected minimal Reynolds-effect package

Send `TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.png` in the email body. Attach the PDF
only if a vector-quality copy is useful.

## What was corrected

- The Re_c=10^4 high-viscosity control is panel (a).
- All four Reynolds values are shown side by side.
- Vorticity PNGs are flipped vertically for conventional Cartesian +y-up
  display. The original ML rasters store ascending physical y from the first
  image row downward; no flow-field or vorticity sign was changed.
- Panel (e) now shows absolute C_L and C_D, not zero-referenced percent changes.

## Defensible message

At fixed f180 resolution and over t=3-6, increasing Re_c from 10^4 to 10^5
changes reconstructed mean lift by about +0.58% and mean drag by about +0.35%.
The drag change is smaller than temporal variability. In contrast, wake
topology and wake-enstrophy level change strongly and non-monotonically, while
shock standoff changes by less than 0.4% of its Re_c=10^4 value.

## Qualifications

- Loads are reconstructed trends from a control-volume momentum balance; the
  native MFC immersed-boundary force records were NaN.
- The direct Reynolds comparison uses f180 and t=3-6.
- Re_c=10^6 is shown only as later-time f270 context and is not connected to
  the direct trend.
- Wake statistics are descriptive because their t=3-6 series are still drifting.
""",
        encoding="utf-8",
    )

    print(f"PNG={png}")
    print(f"PDF={pdf}")
    print(f"README={readme}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--force-output", type=Path, required=True)
    result.add_argument("--flow-output", type=Path, required=True)
    result.add_argument(
        "--image-source",
        type=Path,
        required=True,
        help="ml_dataset directory or MFC_A40_CV_LITE_476.tar.gz",
    )
    result.add_argument("--output", type=Path, required=True)
    return result


if __name__ == "__main__":
    build(parser().parse_args())
