#!/usr/bin/env python3
"""Export fixed-grid MFC fields and physics labels for vision training.

Each sample contains physical flow channels, render-free masks, two canonical
PNG inputs, exact simulation metadata, and a source pointer.  Vortex labels
use the Stage-8 definitions from ``research/dart_cfd_pilot``; shock labels are
derived from the upstream density-gradient ridge.  These are weak physical
labels, not hand annotations.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from cv_physics_labels import (
    VORTEX_CONFIG,
    associate_vortex_cores,
    bow_shock_labels,
    extract_vortex_cores,
    geometry_fluid_mask,
    vortex_diagnostics,
    vortex_heatmaps,
)
from raw_restart_reader import assemble_raw, discover_raw_fields


SCHEMA_VERSION = "mfc-cv-physics-v1"
DART_REFERENCE_COMMIT = "0434ade0d59771c211080b429d53bf9635fe3f8a"
VIEW = (-1.25, 4.75, -1.25, 4.75)
SHOCK_GATE_MIN_TIME = 1.0
FIELD_NAMES = (
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
)


@dataclass(frozen=True)
class CaseInfo:
    label: str
    display: str
    reynolds: float
    grid: str
    dt: float
    case_dir: Path
    role: str


@dataclass(frozen=True)
class FrameSource:
    case: CaseInfo
    step: int
    source_format: str
    source: Path
    sequence_index: int
    split: str


def read_cases(path: Path) -> list[CaseInfo]:
    cases: list[CaseInfo] = []
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            cases.append(
                CaseInfo(
                    label=row["label"],
                    display=row["display"],
                    reynolds=float(row["reynolds"]),
                    grid=row["grid"],
                    dt=float(row["dt"]),
                    case_dir=Path(row["case_dir"]).resolve(),
                    role=row["role"],
                )
            )
    if not cases:
        raise RuntimeError("case table is empty")
    return cases


def binary_fields(case_dir: Path) -> dict[int, Path]:
    for directory in (case_dir / "binary" / "root", case_dir / "binary" / "p0"):
        if not directory.is_dir():
            continue
        result = {
            int(path.stem): path.resolve()
            for path in directory.glob("[0-9]*.dat")
            if path.stem.isdigit()
            and path.is_file()
            and path.stat().st_size > 0
        }
        if result:
            return dict(sorted(result.items()))
    return {}


def split_sequence(count: int) -> list[str]:
    """Contiguous split with two-frame leakage guards at both boundaries."""

    if count <= 0:
        return []
    if count == 1:
        return ["test"]
    if count < 12:
        cut = max(1, count - 1)
        return ["train"] * cut + ["test"] * (count - cut)
    train_end = max(1, int(math.floor(0.65 * count)))
    guard1_end = min(count, train_end + 2)
    validation_count = max(1, int(math.floor(0.15 * count)))
    validation_end = min(count, guard1_end + validation_count)
    guard2_end = min(count, validation_end + 2)
    result = (
        ["train"] * train_end
        + ["guard"] * (guard1_end - train_end)
        + ["val"] * (validation_end - guard1_end)
        + ["guard"] * (guard2_end - validation_end)
        + ["test"] * (count - guard2_end)
    )
    if "test" not in result:
        result[-1] = "test"
    return result


def discover_frames(cases: list[CaseInfo]) -> tuple[list[FrameSource], list[dict[str, Any]]]:
    result: list[FrameSource] = []
    inventory: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for case in cases:
        raw = discover_raw_fields(case.case_dir)
        binary = binary_fields(case.case_dir)
        # Prefer the raw conservative field when both representations exist.
        sources: dict[int, tuple[str, Path]] = {
            step: ("binary_post_process", path) for step, path in binary.items()
        }
        sources.update(
            {step: ("raw_restart_mpiio", path) for step, path in raw.items()}
        )
        steps = sorted(sources)
        splits = split_sequence(len(steps))
        kept = 0
        duplicates = 0
        for sequence_index, (step, split) in enumerate(zip(steps, splits)):
            source_format, source = sources[step]
            canonical = source.resolve()
            if canonical in seen:
                duplicates += 1
                continue
            seen.add(canonical)
            result.append(
                FrameSource(
                    case=case,
                    step=step,
                    source_format=source_format,
                    source=canonical,
                    sequence_index=sequence_index,
                    split=split,
                )
            )
            kept += 1
        inventory.append(
            {
                "case": case.label,
                "Re_c": case.reynolds,
                "grid": case.grid,
                "raw_fields": len(raw),
                "binary_fields": len(binary),
                "union_fields": len(steps),
                "exported_unique_fields": kept,
                "duplicate_sources_skipped": duplicates,
                "first_step": steps[0] if steps else None,
                "last_step": steps[-1] if steps else None,
            }
        )
    return result, inventory


def as_xy(values: np.ndarray, nx: int, ny: int, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    if array.shape == (nx, ny):
        return array
    if array.shape == (ny, nx):
        return array.T
    raise RuntimeError(f"unexpected {name} shape {array.shape}; expected {(nx, ny)}")


def resample(
    x: np.ndarray,
    y: np.ndarray,
    field: np.ndarray,
    target_x: np.ndarray,
    target_y: np.ndarray,
) -> np.ndarray:
    """Bilinearly interpolate a rectilinear field with vectorized weights."""

    ix = np.clip(np.searchsorted(x, target_x) - 1, 0, len(x) - 2)
    wx = ((target_x - x[ix]) / (x[ix + 1] - x[ix])).astype(np.float32)
    x_interp = (
        field[ix, :] * (1.0 - wx[:, None])
        + field[ix + 1, :] * wx[:, None]
    )
    iy = np.clip(np.searchsorted(y, target_y) - 1, 0, len(y) - 2)
    wy = ((target_y - y[iy]) / (y[iy + 1] - y[iy])).astype(np.float32)
    return np.asarray(
        x_interp[:, iy] * (1.0 - wy[None, :])
        + x_interp[:, iy + 1] * wy[None, :],
        dtype=np.float32,
    )


def load_primitive(frame: FrameSource, assemble: Any) -> tuple[np.ndarray, ...]:
    if frame.source_format == "raw_restart_mpiio":
        assembled = assemble_raw(frame.case.case_dir, frame.step, crop=VIEW, halo=3)
    else:
        assembled = assemble(str(frame.case.case_dir), frame.step, fmt="binary")
    needed = {"rho", "pres", "vel1", "vel2"}
    missing = sorted(needed - set(assembled.variables))
    if missing:
        raise RuntimeError(f"{frame.case.label} step {frame.step} lacks {missing}")
    x = np.asarray(assembled.x_cc, dtype=np.float64)
    y = np.asarray(assembled.y_cc, dtype=np.float64)
    xi = np.flatnonzero((x >= VIEW[0]) & (x <= VIEW[1]))
    yi = np.flatnonzero((y >= VIEW[2]) & (y <= VIEW[3]))
    if xi.size < 8 or yi.size < 8:
        raise RuntimeError(f"field does not cover training view: {frame.source}")
    xs = slice(int(xi[0]), int(xi[-1]) + 1)
    ys = slice(int(yi[0]), int(yi[-1]) + 1)
    nx, ny = len(x), len(y)
    fields = [
        as_xy(assembled.variables[name], nx, ny, name)[xs, ys]
        for name in ("rho", "pres", "vel1", "vel2")
    ]
    if not all(np.isfinite(value).all() for value in fields):
        raise RuntimeError(f"non-finite primitive field: {frame.source}")
    return x[xs], y[ys], *fields


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def save_inputs(
    schlieren_path: Path,
    vorticity_path: Path,
    schlieren: np.ndarray,
    omega: np.ndarray,
    fluid: np.ndarray,
) -> None:
    from PIL import Image
    import matplotlib

    scalar = np.clip(schlieren / 65.0, 0.0, 1.0)
    scalar = np.where(fluid, scalar, 0.0)
    gray = np.asarray(np.rint(255.0 * scalar).T, dtype=np.uint8)
    Image.fromarray(gray, mode="L").save(schlieren_path, optimize=True)
    normalized = np.clip((omega + 17.0) / 34.0, 0.0, 1.0)
    rgba = matplotlib.colormaps["RdBu_r"](normalized.T, bytes=True)
    rgb = np.asarray(rgba[..., :3], dtype=np.uint8)
    rgb[~fluid.T] = 0
    Image.fromarray(rgb, mode="RGB").save(vorticity_path, optimize=True)


def save_label_rasters(
    shock_path: Path,
    ridge_path: Path,
    positive_path: Path,
    negative_path: Path,
    instances_path: Path,
    shock: np.ndarray,
    ridge: np.ndarray,
    positive: np.ndarray,
    negative: np.ndarray,
    instances: np.ndarray,
) -> None:
    """Write lossless, fixed-encoding raster targets for image pipelines."""

    from PIL import Image

    Image.fromarray(np.asarray(255 * shock.T, dtype=np.uint8), mode="L").save(
        shock_path, optimize=True
    )
    Image.fromarray(np.asarray(255 * ridge.T, dtype=np.uint8), mode="L").save(
        ridge_path, optimize=True
    )
    for path, heatmap in ((positive_path, positive), (negative_path, negative)):
        encoded = np.asarray(
            np.rint(65535.0 * np.clip(heatmap.T, 0.0, 1.0)), dtype=np.uint16
        )
        Image.fromarray(encoded).save(path, optimize=True)
    Image.fromarray(np.asarray(instances.T, dtype=np.uint16)).save(
        instances_path, optimize=True
    )


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def dataset_id(frame: FrameSource) -> str:
    return f"{frame.case.label}_s{frame.step:09d}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-table", type=Path, required=True)
    parser.add_argument("--mfc-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--max-frames", type=int, default=0)
    args = parser.parse_args()
    if args.width < 128 or args.height < 128:
        parser.error("training raster dimensions must be at least 128")
    cases = read_cases(args.case_table.resolve())
    frames, inventory = discover_frames(cases)
    if len(frames) < 300:
        raise RuntimeError(f"only {len(frames)} unique CFD frames were found; expected at least 300")
    reynolds = {int(round(frame.case.reynolds)) for frame in frames}
    if not {10_000, 50_000, 100_000, 1_000_000}.issubset(reynolds):
        raise RuntimeError(f"training inventory lacks a Reynolds class: {sorted(reynolds)}")
    if args.max_frames:
        frames = frames[: args.max_frames]
    if args.check_only:
        print(json.dumps({"status": "PASS", "frames": len(frames), "inventory": inventory}, sort_keys=True))
        return 0

    output = args.output.resolve()
    tensor_dir = output / "tensors"
    schlieren_dir = output / "images" / "schlieren"
    vorticity_dir = output / "images" / "vorticity"
    label_root = output / "labels"
    shock_dir = label_root / "shock_mask"
    ridge_dir = label_root / "shock_ridge"
    positive_dir = label_root / "vortex_positive_heatmap"
    negative_dir = label_root / "vortex_negative_heatmap"
    instances_dir = label_root / "vortex_instances"
    for directory in (
        tensor_dir,
        schlieren_dir,
        vorticity_dir,
        shock_dir,
        ridge_dir,
        positive_dir,
        negative_dir,
        instances_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    mfc_root = args.mfc_root.resolve()
    sys.path.insert(0, str(mfc_root / "toolchain"))
    from mfc.viz.reader import assemble

    target_x = np.linspace(VIEW[0], VIEW[1], args.width, dtype=np.float64)
    target_y = np.linspace(VIEW[2], VIEW[3], args.height, dtype=np.float64)
    # ``fluid`` excludes a three-cell IB guard and is therefore the validity
    # mask for differentiated labels. ``body`` is only the exact geometry;
    # guard cells must not be mislabeled as solid training pixels.
    fluid = geometry_fluid_mask(target_x, target_y)
    body = ~geometry_fluid_mask(target_x, target_y, guard_cells=0.0)
    from PIL import Image

    Image.fromarray(np.asarray(255 * fluid.T, dtype=np.uint8), mode="L").save(
        label_root / "label_valid_mask.png", optimize=True
    )
    Image.fromarray(np.asarray(255 * body.T, dtype=np.uint8), mode="L").save(
        label_root / "body_mask.png", optimize=True
    )
    manifest_rows: list[dict[str, Any]] = []
    vortex_rows: list[dict[str, Any]] = []
    shock_rows: list[dict[str, Any]] = []
    tracks: dict[str, dict[int, list[dict[str, Any]]]] = {}
    next_ids: dict[str, int] = {}
    prior: dict[str, FrameSource] = {}
    shock_pass = 0
    shock_gate_eligible = 0
    shock_gate_pass = 0
    normalization_count = 0
    normalization_sum = np.zeros(len(FIELD_NAMES), dtype=np.float64)
    normalization_square_sum = np.zeros(len(FIELD_NAMES), dtype=np.float64)
    normalization_min = np.full(len(FIELD_NAMES), np.inf, dtype=np.float64)
    normalization_max = np.full(len(FIELD_NAMES), -np.inf, dtype=np.float64)
    for global_index, frame in enumerate(frames):
        identifier = dataset_id(frame)
        x, y, rho, pressure, u, v = load_primitive(frame, assemble)
        rho = resample(x, y, rho, target_x, target_y)
        pressure = resample(x, y, pressure, target_x, target_y)
        u = resample(x, y, u, target_x, target_y)
        v = resample(x, y, v, target_x, target_y)
        if np.any(rho[fluid] <= 0.0) or np.any(pressure[fluid] <= 0.0):
            raise RuntimeError(f"non-positive primitive value in {identifier}")
        sound_speed = np.sqrt(
            np.maximum(1.4 * pressure / np.maximum(rho, 1.0e-12), 1.0e-12)
        )
        mach = np.hypot(u, v) / sound_speed
        drho_dx, drho_dy = np.gradient(rho, target_x, target_y, edge_order=2)
        schlieren = np.hypot(drho_dx, drho_dy).astype(np.float32)
        diagnostics = vortex_diagnostics(target_x, target_y, u, v)
        cores, thresholds = extract_vortex_cores(
            target_x, target_y, diagnostics, fluid
        )
        threshold_record = {
            key: float(value) if math.isfinite(float(value)) else None
            for key, value in thresholds.items()
        }
        previous = prior.get(frame.case.label)
        reset_tracks = (
            previous is None
            or frame.split != previous.split
            or frame.step * frame.case.dt - previous.step * previous.case.dt > 0.151
        )
        if reset_tracks:
            tracks[frame.case.label] = {}
        associated, updated_tracks, next_id = associate_vortex_cores(
            cores,
            frame.sequence_index,
            tracks.get(frame.case.label, {}),
            next_ids.get(frame.case.label, 1),
        )
        tracks[frame.case.label] = updated_tracks
        next_ids[frame.case.label] = next_id
        prior[frame.case.label] = frame
        for row in associated:
            row.update(
                dataset_id=identifier,
                case=frame.case.label,
                split=frame.split,
                source_step=frame.step,
                time=frame.step * frame.case.dt,
                reference_id=f"{frame.case.label}-{row['reference_id']}",
                label_origin="DART_STAGE8_COMPATIBLE_FIXED_GRID",
            )
            vortex_rows.append(row)
        positive, negative, instances = vortex_heatmaps(
            target_x, target_y, associated
        )
        shock_mask, shock_ridge, shock_info = bow_shock_labels(
            target_x, target_y, schlieren, fluid
        )
        if shock_info["status"] == "PASS":
            shock_pass += 1
        if frame.step * frame.case.dt >= SHOCK_GATE_MIN_TIME - 1.0e-9:
            shock_gate_eligible += 1
            if shock_info["status"] == "PASS":
                shock_gate_pass += 1
        shock_row = {
            "dataset_id": identifier,
            "case": frame.case.label,
            "split": frame.split,
            "source_step": frame.step,
            "time": frame.step * frame.case.dt,
            **shock_info,
            "label_origin": "DENSITY_GRADIENT_RIDGE_FIXED_GRID",
        }
        shock_rows.append(shock_row)
        stack = np.stack(
            [
                rho,
                pressure,
                u,
                v,
                mach,
                schlieren,
                diagnostics["omega"],
                diagnostics["lambda_ci"],
                diagnostics["q"],
                diagnostics["omega_ratio"],
                diagnostics["gamma2"],
            ],
            axis=0,
        ).transpose(0, 2, 1)
        stack[:, ~fluid.T] = 0.0
        if frame.split == "train":
            valid_values = np.asarray(stack[:, fluid.T], dtype=np.float64)
            normalization_count += valid_values.shape[1]
            normalization_sum += np.sum(valid_values, axis=1, dtype=np.float64)
            normalization_square_sum += np.sum(
                valid_values * valid_values, axis=1, dtype=np.float64
            )
            normalization_min = np.minimum(
                normalization_min, np.min(valid_values, axis=1)
            )
            normalization_max = np.maximum(
                normalization_max, np.max(valid_values, axis=1)
            )
        tensor_path = tensor_dir / f"{identifier}.npz"
        np.savez_compressed(
            tensor_path,
            fields=np.asarray(stack, dtype=np.float32),
            field_names=np.asarray(FIELD_NAMES),
            x=np.asarray(target_x, dtype=np.float32),
            y=np.asarray(target_y, dtype=np.float32),
            fluid_mask=np.asarray(fluid.T, dtype=np.uint8),
            label_valid_mask=np.asarray(fluid.T, dtype=np.uint8),
            body_mask=np.asarray(body.T, dtype=np.uint8),
            shock_mask=np.asarray(shock_mask.T, dtype=np.uint8),
            shock_ridge=np.asarray(shock_ridge.T, dtype=np.uint8),
            vortex_positive_heatmap=np.asarray(positive.T, dtype=np.float16),
            vortex_negative_heatmap=np.asarray(negative.T, dtype=np.float16),
            vortex_instances=np.asarray(instances.T, dtype=np.uint16),
        )
        schlieren_path = schlieren_dir / f"{identifier}.png"
        vorticity_path = vorticity_dir / f"{identifier}.png"
        save_inputs(
            schlieren_path,
            vorticity_path,
            schlieren,
            diagnostics["omega"],
            fluid,
        )
        shock_path = shock_dir / f"{identifier}.png"
        ridge_path = ridge_dir / f"{identifier}.png"
        positive_path = positive_dir / f"{identifier}.png"
        negative_path = negative_dir / f"{identifier}.png"
        instances_path = instances_dir / f"{identifier}.png"
        save_label_rasters(
            shock_path,
            ridge_path,
            positive_path,
            negative_path,
            instances_path,
            shock_mask,
            shock_ridge,
            positive,
            negative,
            instances,
        )
        manifest_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "dataset_id": identifier,
                "global_index": global_index,
                "sequence_index": frame.sequence_index,
                "case": frame.case.label,
                "display": frame.case.display,
                "Re_c": frame.case.reynolds,
                "Mach_inf": 3.0,
                "alpha_deg": 40.0,
                "grid": frame.case.grid,
                "role": frame.case.role,
                "source_step": frame.step,
                "time": frame.step * frame.case.dt,
                "dt": frame.case.dt,
                "split": frame.split,
                "source_format": frame.source_format,
                "source_path": str(frame.source),
                "tensor": str(tensor_path.relative_to(output)),
                "schlieren_png": str(schlieren_path.relative_to(output)),
                "vorticity_png": str(vorticity_path.relative_to(output)),
                "shock_mask_png": str(shock_path.relative_to(output)),
                "shock_ridge_png": str(ridge_path.relative_to(output)),
                "vortex_positive_heatmap_png": str(
                    positive_path.relative_to(output)
                ),
                "vortex_negative_heatmap_png": str(
                    negative_path.relative_to(output)
                ),
                "vortex_instances_png": str(instances_path.relative_to(output)),
                "tensor_sha256": sha256(tensor_path),
                "vortex_cores": len(associated),
                "shock_status": shock_info["status"],
                "vortex_thresholds": threshold_record,
            }
        )
        print(
            f"CV_FRAME {global_index + 1}/{len(frames)} id={identifier} "
            f"vortices={len(associated)} shock={shock_info['status']}",
            flush=True,
        )

    manifest_path = output / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as stream:
        for row in manifest_rows:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    write_csv(output / "vortex_catalogue.csv", vortex_rows)
    write_csv(output / "shock_catalogue.csv", shock_rows)
    stage8_fields = [
        "frame_index",
        "source_step",
        "time",
        "reference_id",
        "x_physical",
        "y_physical",
        "rotation_sign",
        "omega",
        "lambda_ci",
        "q",
        "omega_ratio",
        "gamma2",
        "criterion_support",
        "confidence",
        "association_cost",
        "prediction_error",
    ]
    catalogue_dir = output / "catalogues"
    catalogue_dir.mkdir(parents=True, exist_ok=True)
    stage8_catalogues: dict[str, str] = {}
    for case in cases:
        selected = [row for row in vortex_rows if row["case"] == case.label]
        path = catalogue_dir / f"{case.label}_stage8_catalogue.csv"
        write_csv(path, selected, stage8_fields)
        stage8_catalogues[case.label] = str(path.relative_to(output))
    write_csv(
        output / "splits.csv",
        [
            {
                "dataset_id": row["dataset_id"],
                "case": row["case"],
                "time": row["time"],
                "split": row["split"],
            }
            for row in manifest_rows
        ],
    )
    split_names = ("train", "val", "test", "guard")
    balance_rows = [
        {
            "case": case.label,
            "Re_c": case.reynolds,
            "grid": case.grid,
            "split": split,
            "frames": sum(
                row["case"] == case.label and row["split"] == split
                for row in manifest_rows
            ),
        }
        for case in cases
        for split in split_names
    ]
    write_csv(output / "dataset_balance.csv", balance_rows)
    if normalization_count <= 0:
        raise RuntimeError("no valid training pixels were available for normalization")
    normalization_mean = normalization_sum / normalization_count
    normalization_variance = np.maximum(
        normalization_square_sum / normalization_count - normalization_mean**2,
        0.0,
    )
    normalization_std = np.sqrt(normalization_variance)
    normalization = {
        "source_split": "train",
        "mask": "label_valid_mask",
        "valid_pixels_per_channel": normalization_count,
        "channels": {
            name: {
                "mean": float(normalization_mean[index]),
                "std": float(normalization_std[index]),
                "minimum": float(normalization_min[index]),
                "maximum": float(normalization_max[index]),
            }
            for index, name in enumerate(FIELD_NAMES)
        },
    }
    (output / "normalization.json").write_text(
        json.dumps(normalization, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    loader_source = Path(__file__).with_name("cv_dataset_loader.py")
    if not loader_source.is_file():
        raise RuntimeError(f"dataset loader is missing: {loader_source}")
    shutil.copy2(loader_source, output / "cv_dataset_loader.py")
    split_counts = {
        name: sum(row["split"] == name for row in manifest_rows)
        for name in split_names
    }
    gates = {
        "minimum_unique_frames": len(manifest_rows) >= 300,
        "all_reynolds_classes": {10_000, 50_000, 100_000, 1_000_000}.issubset(reynolds),
        "nonempty_train_val_test": all(split_counts[name] > 0 for name in ("train", "val", "test")),
        "shock_ridge_detected_post_startup": (
            shock_gate_eligible > 0
            and shock_gate_pass >= math.ceil(0.8 * shock_gate_eligible)
        ),
        "vortex_catalogue_nonempty": len(vortex_rows) > 0,
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if all(gates.values()) else "FAILED_GATES",
        "frames": len(manifest_rows),
        "shape_hw": [args.height, args.width],
        "field_dtype": "float32",
        "field_names": FIELD_NAMES,
        "raster_label_encodings": {
            "binary_masks": "uint8 PNG with values 0 and 255",
            "vortex_heatmaps": "uint16 PNG mapping [0,1] to [0,65535]",
            "vortex_instances": "uint16 PNG; zero is background and positive values are per-frame instances",
        },
        "normalization": "normalization.json; computed on train split and label-valid pixels only",
        "coordinate_units": "chord lengths",
        "time_units": "MFC nondimensional time",
        "view_xy": VIEW,
        "split_counts": split_counts,
        "case_split_counts": {
            case.label: {
                split: sum(
                    row["case"] == case.label and row["split"] == split
                    for row in manifest_rows
                )
                for split in split_names
            }
            for case in cases
        },
        "shock_pass_frames": shock_pass,
        "shock_gate_min_time": SHOCK_GATE_MIN_TIME,
        "shock_gate_eligible_frames": shock_gate_eligible,
        "shock_gate_pass_frames": shock_gate_pass,
        "shock_detection_fraction_post_startup": (
            shock_gate_pass / shock_gate_eligible if shock_gate_eligible else None
        ),
        "vortex_catalogue_rows": len(vortex_rows),
        "inventory": inventory,
        "gates": gates,
        "compatibility": {
            "project": "research/dart_cfd_pilot",
            "reference_commit": DART_REFERENCE_COMMIT,
            "vortex_schema": "Stage 8 catalogue / Stage 11 point detections",
            "vortex_config": VORTEX_CONFIG,
            "per_case_stage8_catalogues": stage8_catalogues,
        },
        "label_qualification": "PHYSICS_DERIVED_WEAK_LABELS_REQUIRING_MANUAL_AUDIT",
        "limitations": [
            "Vortex targets describe two-dimensional core locations, not three-dimensional vortex tubes.",
            "Shock and vortex labels are algorithmic weak labels, not hand-annotated ground truth.",
            "Dataset vorticity is consistently differentiated from velocity; post-process omega3 is not mixed into the label definition.",
            "Guard frames must not be used for training, validation, or testing.",
            "Retained Re=1e6 fields are temporally imbalanced relative to the lower-Re sequences; use dataset_balance.csv and matched-time subsets before attributing learned differences to Reynolds number.",
            "float32 tensors are resampled training representations; authoritative float64 fields remain at source_path.",
        ],
    }
    (output / "dataset_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "DATASET_CARD.md").write_text(
        "# MFC Mach-3 alpha-40 physics-labelled vision dataset\n\n"
        f"Schema: `{SCHEMA_VERSION}`. Frames: {len(manifest_rows)}. "
        f"Raster: {args.width}x{args.height}.\n\n"
        "Each tensor stores eleven physical channels plus fluid/body/label-validity, shock-ribbon, "
        "shock-ridge, signed-vortex heatmap, and vortex-instance targets. Lossless PNG copies of "
        "the raster targets are under `labels/`. PNG inputs "
        "use fixed scales (schlieren 0..65; vorticity -17..17), so intensity is not "
        "normalized independently per frame.\n\n"
        "Vortex definitions match the frozen DART Stage-8 physics catalogue. Labels "
        "use vorticity differentiated from velocity because raw restart files do not "
        "contain post-processed omega3. They are weak physical annotations and require "
        "manual audit before publication. "
        "Use only `train`, `val`, and `test`; exclude `guard` frames to prevent direct "
        "temporal leakage. Channel statistics in `normalization.json` use only valid "
        "training pixels.\n\n"
        "```python\n"
        "from cv_dataset_loader import MFCCVDataset\n"
        "train = MFCCVDataset('.', split='train', normalize=True)\n"
        "sample = train[0]  # fields: (channels, height, width)\n"
        "```\n",
        encoding="utf-8",
    )
    (output / "DATASET_OK.txt").write_text(
        f"status={report['status']}\nframes={len(manifest_rows)}\n"
        f"vortex_rows={len(vortex_rows)}\nshock_pass_frames={shock_pass}\n"
        f"shock_gate_eligible_frames={shock_gate_eligible}\n"
        f"shock_gate_pass_frames={shock_gate_pass}\n"
        f"manifest_sha256={sha256(manifest_path)}\n",
        encoding="utf-8",
    )
    if report["status"] != "PASS":
        raise RuntimeError(f"computer-vision dataset gates failed: {gates}")
    print(f"MFC_CV_DATASET=PASS output={output} frames={len(manifest_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
