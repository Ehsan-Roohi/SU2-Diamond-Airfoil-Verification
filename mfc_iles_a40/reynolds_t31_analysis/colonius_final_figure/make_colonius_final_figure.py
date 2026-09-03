#!/usr/bin/env python3
"""Build the email-ready fixed-grid Reynolds figure from completed outputs.

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
from PIL import Image


CASES = ("re1e4_f180", "re5e4_f180", "re1e5_f180")
RE_LABELS = (r"$10^4$", r"$5\times10^4$", r"$10^5$")
SNAPSHOT_STEP = 21600


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def force_rows(force_output: Path) -> dict[str, dict[str, str]]:
    rows = read_csv(force_output / "control_volume_force_summary.csv")
    selected = {
        row["case"]: row
        for row in rows
        if row["case"] in CASES
        and row["control_volume"] == "nominal"
        and row["comparison_role"] == "same_grid_reynolds"
    }
    if tuple(selected) != CASES:
        missing = sorted(set(CASES) - set(selected))
        raise RuntimeError(f"missing nominal force summaries: {missing}")
    return selected


def flow_rows(flow_output: Path) -> dict[str, dict[str, dict[str, str]]]:
    rows = read_csv(flow_output / "tim_colonius_same_grid_reynolds_summary.csv")
    selected: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        if row["case"] in CASES and row["window"] == "post_startup_t3_t6":
            selected.setdefault(row["metric"], {})[row["case"]] = row
    required = (
        "wake_enstrophy",
        "wake_abs_vorticity_p99",
        "wake_pressure_rms",
        "shock_standoff_over_c",
    )
    for metric in required:
        missing = sorted(set(CASES) - set(selected.get(metric, {})))
        if missing:
            raise RuntimeError(f"missing {metric} summaries: {missing}")
    return selected


def open_snapshot(source: Path, case: str) -> Image.Image:
    name = f"images/vorticity/{case}_s{SNAPSHOT_STEP:09d}.png"
    if source.is_dir():
        path = source / name
        if not path.is_file():
            raise FileNotFoundError(path)
        return Image.open(path).convert("RGB")

    if not source.is_file():
        raise FileNotFoundError(source)
    with tarfile.open(source, "r:gz") as archive:
        candidates = (name, f"./{name}")
        member = next((candidate for candidate in candidates if candidate in archive.getnames()), None)
        if member is None:
            raise FileNotFoundError(f"{name} not found in {source}")
        payload = archive.extractfile(member)
        if payload is None:
            raise RuntimeError(f"could not read {member} from {source}")
        return Image.open(io.BytesIO(payload.read())).convert("RGB")


def change_and_variability(
    rows: dict[str, dict[str, str]], mean_key: str, std_key: str
) -> tuple[list[float], list[float]]:
    base_mean = float(rows[CASES[0]][mean_key])
    base_std = float(rows[CASES[0]][std_key])
    change: list[float] = []
    spread: list[float] = []
    for index, case in enumerate(CASES):
        mean = float(rows[case][mean_key])
        std = float(rows[case][std_key])
        change.append(100.0 * (mean / base_mean - 1.0))
        # The baseline is the reference rather than a measured zero-change
        # point. For the other cases, show quadrature-combined temporal SD.
        spread.append(0.0 if index == 0 else 100.0 * math.hypot(std, base_std) / abs(base_mean))
    return change, spread


def configure_axis(ax: plt.Axes, ylabel: str, ylim: tuple[float, float]) -> None:
    ax.axhline(0.0, color="#6b7280", linewidth=0.9, linestyle="--", zorder=0)
    ax.set_xticks(range(3), RE_LABELS)
    ax.set_xlabel(r"Chord Reynolds number, $Re_c$")
    ax.set_ylabel(ylabel)
    ax.set_ylim(*ylim)
    ax.grid(axis="y", color="#d1d5db", linewidth=0.7, alpha=0.7)
    ax.spines[["top", "right"]].set_visible(False)


def plot_series(
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
    images = [open_snapshot(args.image_source, case) for case in CASES]
    args.output.mkdir(parents=True, exist_ok=True)

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.labelsize": 10,
            "axes.titlesize": 11,
            "legend.fontsize": 9,
            "pdf.fonttype": 42,
        }
    )
    fig = plt.figure(figsize=(15.0, 9.2), facecolor="white")
    grid = fig.add_gridspec(
        2,
        3,
        height_ratios=(1.25, 0.92),
        left=0.055,
        right=0.985,
        bottom=0.145,
        top=0.875,
        wspace=0.28,
        hspace=0.31,
    )

    fig.suptitle(
        r"Mach 3 flow over a $40^{\circ}$ diamond airfoil",
        x=0.055,
        y=0.965,
        ha="left",
        fontsize=19,
        fontweight="bold",
        color="#111827",
    )
    fig.text(
        0.055,
        0.918,
        "Fixed-grid Reynolds comparison: integral loads change weakly while the separated wake changes strongly",
        ha="left",
        fontsize=13,
        color="#374151",
    )

    for index, (image, re_label) in enumerate(zip(images, RE_LABELS)):
        ax = fig.add_subplot(grid[0, index])
        ax.imshow(image)
        ax.set_axis_off()
        ax.set_title(rf"({chr(97 + index)})  $Re_c={re_label[1:-1]}$", pad=8, fontweight="bold")
        ax.text(
            0.018,
            0.025,
            r"$t=6$; fixed $\omega_z$ raster scale $[-17,17]$",
            transform=ax.transAxes,
            fontsize=8.5,
            color="#111827",
            bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "alpha": 0.84, "edgecolor": "none"},
        )

    # Load changes from the completed nominal control volume.
    cl, cl_sd = change_and_variability(force, "CL_mean", "CL_temporal_std")
    cd, cd_sd = change_and_variability(force, "CD_mean", "CD_temporal_std")
    ax = fig.add_subplot(grid[1, 0])
    configure_axis(ax, r"Change from $Re_c=10^4$ (%)", (-0.75, 1.25))
    plot_series(ax, cl, cl_sd, r"$C_L$", "#2563eb", "o")
    plot_series(ax, cd, cd_sd, r"$C_D$", "#ea580c", "s")
    ax.legend(loc="upper left", frameon=False, ncol=2)
    ax.set_title("(d) Reconstructed integral loads", loc="left", fontweight="bold")
    ax.annotate(
        rf"$\Delta C_L={cl[-1]:+.2f}\%$",
        (2, cl[-1]),
        xytext=(-10, 18),
        textcoords="offset points",
        ha="right",
        color="#2563eb",
        fontsize=9,
    )

    # Wake descriptors are intentionally shown separately because their
    # response is an order of magnitude larger than the load response.
    wake_enstrophy = flow["wake_enstrophy"]
    wake_p99 = flow["wake_abs_vorticity_p99"]
    ens, ens_sd = change_and_variability(wake_enstrophy, "mean", "temporal_std")
    p99, p99_sd = change_and_variability(wake_p99, "mean", "temporal_std")
    ax = fig.add_subplot(grid[1, 1])
    configure_axis(ax, r"Change from $Re_c=10^4$ (%)", (-22.0, 55.0))
    plot_series(ax, ens, ens_sd, "Wake enstrophy", "#9333ea", "o")
    plot_series(ax, p99, p99_sd, r"Wake $|\omega_z|_{99}$", "#0891b2", "s")
    ax.legend(loc="upper right", frameon=False)
    ax.set_title("(e) Wake dynamics", loc="left", fontweight="bold")
    ax.annotate(
        rf"{ens[1]:+.1f}\%",
        (1, ens[1]),
        xytext=(7, 4),
        textcoords="offset points",
        color="#9333ea",
        fontsize=9,
    )

    shock_rows = flow["shock_standoff_over_c"]
    pressure_rows = flow["wake_pressure_rms"]
    shock, shock_sd = change_and_variability(shock_rows, "mean", "temporal_std")
    pressure, pressure_sd = change_and_variability(pressure_rows, "mean", "temporal_std")
    ax = fig.add_subplot(grid[1, 2])
    configure_axis(ax, r"Change from $Re_c=10^4$ (%)", (-3.2, 3.2))
    plot_series(ax, shock, shock_sd, "Shock standoff", "#4b5563", "o")
    plot_series(ax, pressure, pressure_sd, "Wake pressure RMS", "#16a34a", "s")
    ax.legend(loc="upper left", frameon=False)
    ax.set_title("(f) Shock / pressure response", loc="left", fontweight="bold")

    footer = (
        r"Fixed grid: f180. Statistics: $t\in[3,6]$ (61 samples); snapshots: $t=6$. "
        r"Whiskers: quadrature temporal SD (not confidence intervals). "
        "Loads are control-volume momentum-balance reconstructions because native IB forces are NaN. "
        "Wake statistics are descriptive; this window is not stationary."
    )
    fig.text(0.055, 0.052, footer, ha="left", va="bottom", fontsize=8.8, color="#4b5563", wrap=True)
    fig.text(
        0.985,
        0.052,
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
        """# Tim Colonius — minimal Reynolds-effect package

Send `TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.png` in the email body. Attach the PDF
only if a vector-quality copy is useful.

## Defensible message

At fixed f180 resolution and over t=3–6, increasing Re_c from 10^4 to 10^5
changes the reconstructed mean lift by about +0.58% and mean drag by about
+0.35%. The drag change is smaller than the temporal variability. In contrast,
the wake topology and wake-enstrophy level change strongly and non-monotonically,
while shock standoff changes by less than 0.4% of the Re_c=10^4 value.

## Required qualifications

- Loads are reconstructed trends from a control-volume momentum balance; the
  native MFC immersed-boundary force records were NaN.
- The three direct Reynolds cases use the same f180 grid and t=3–6 window.
- Whiskers are temporal standard-deviation propagation, not confidence intervals.
- Wake statistics are descriptive because their t=3–6 series are still drifting.
- Re_c=10^6 is omitted from the trend because it uses f270 and a later window.
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
