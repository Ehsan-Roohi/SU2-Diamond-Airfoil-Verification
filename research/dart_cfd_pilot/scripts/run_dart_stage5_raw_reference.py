#!/usr/bin/env python3
"""Build a vortex reference catalogue from raw MFC fields.

The reference is independent of the plotted RGB movie. Candidate cores must
simultaneously have high swirling strength and high absolute vorticity. Their
centres are spatially suppressed and associated through time using signed
vorticity and bounded physical displacement.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


AIRFOIL_HALF_HEIGHT = 0.0702704174


def expected_steps(config: dict) -> list[int]:
    return list(
        range(
            int(config["step_start"]),
            int(config["step_stop"]) + 1,
            int(config["step_stride"]),
        )
    )


def geometry_fluid_mask(x, y):
    import numpy as np

    dx = float(np.min(np.diff(x)))
    dy = float(np.min(np.diff(y)))
    pad = 3.0 * max(dx, dy)
    xx = x[:, None]
    yy = y[None, :]
    chord_band = (xx >= -pad) & (xx <= 1.0 + pad)
    clipped_x = np.clip(xx, 0.0, 1.0)
    half_height = AIRFOIL_HALF_HEIGHT * (1.0 - np.abs(2.0 * clipped_x - 1.0))
    return ~(chord_band & (np.abs(yy) <= half_height + pad))


def velocity_diagnostics(x, y, vel1, vel2):
    """Return derived vorticity and 2-D swirling strength."""
    import numpy as np

    du_dx, du_dy = np.gradient(vel1, x, y, edge_order=2)
    dv_dx, dv_dy = np.gradient(vel2, x, y, edge_order=2)
    omega = dv_dx - du_dy
    trace = du_dx + dv_dy
    determinant = du_dx * dv_dy - du_dy * dv_dx
    discriminant = trace * trace - 4.0 * determinant
    lambda_ci = 0.5 * np.sqrt(np.maximum(-discriminant, 0.0))
    return omega, lambda_ci


def correlation(a, b, mask) -> float | None:
    import numpy as np

    valid = mask & np.isfinite(a) & np.isfinite(b)
    av = a[valid][::8]
    bv = b[valid][::8]
    if av.size < 10 or float(np.std(av)) == 0.0 or float(np.std(bv)) == 0.0:
        return None
    return float(np.corrcoef(av, bv)[0, 1])


def assess_vorticity_consistency(per_frame: list[dict], config: dict) -> tuple[bool, dict]:
    """Require the configured number of agreeing frames, while retaining outliers."""
    threshold = float(config["minimum_vorticity_correlation"])
    required = int(config["minimum_consistency_frames"])
    evaluated = []
    passing = []
    failures = []
    for frame in per_frame:
        correlation_value = frame.get("vorticity_correlation")
        absolute = None
        if correlation_value is not None and math.isfinite(float(correlation_value)):
            absolute = abs(float(correlation_value))
            evaluated.append(absolute)
        if absolute is not None and absolute >= threshold:
            passing.append(frame)
        else:
            failures.append(
                {
                    "frame_index": frame.get("frame_index"),
                    "source_step": frame.get("source_step"),
                    "time": frame.get("time"),
                    "absolute_vorticity_correlation": absolute,
                    "reason": (
                        "below_threshold"
                        if absolute is not None
                        else "not_evaluated"
                    ),
                }
            )
    summary = {
        "threshold": threshold,
        "required_passing_frames": required,
        "evaluated_frames": len(evaluated),
        "passing_frames": len(passing),
        "failing_frames": len(failures),
        "minimum_absolute_correlation": min(evaluated) if evaluated else None,
        "median_absolute_correlation": (
            statistics.median(evaluated) if evaluated else None
        ),
        "failures": failures,
    }
    return len(passing) >= required, summary


def extract_cores(x, y, omega, lambda_ci, fluid, config: dict, quantile: float | None = None) -> tuple[list[dict], dict]:
    import numpy as np

    q = float(config["lambda_ci_quantile"] if quantile is None else quantile)
    q_omega = float(config["absolute_vorticity_quantile"] if quantile is None else quantile)
    valid = fluid & np.isfinite(omega) & np.isfinite(lambda_ci)
    rotating = valid & (lambda_ci > 0.0)
    if not rotating.any():
        return [], {"lambda_ci_threshold": None, "absolute_vorticity_threshold": None}
    lambda_threshold = float(np.quantile(lambda_ci[rotating], q))
    omega_threshold = float(np.quantile(np.abs(omega[rotating]), q_omega))
    candidate = rotating & (lambda_ci >= lambda_threshold) & (np.abs(omega) >= omega_threshold)
    indices = np.argwhere(candidate)
    if not indices.size:
        return [], {
            "lambda_ci_threshold": lambda_threshold,
            "absolute_vorticity_threshold": omega_threshold,
        }
    strength = lambda_ci[candidate] * np.abs(omega[candidate])
    order = np.argsort(strength)[::-1]
    separation = float(config["minimum_core_separation"])
    maximum = int(config["maximum_cores_per_frame"])
    bins: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    accepted: list[dict] = []
    xmin, ymin = float(x[0]), float(y[0])
    for position in order:
        i, j = (int(value) for value in indices[position])
        x_value, y_value = float(x[i]), float(y[j])
        bx = math.floor((x_value - xmin) / separation)
        by = math.floor((y_value - ymin) / separation)
        too_close = any(
            math.hypot(x_value - other_x, y_value - other_y) < separation
            for nx in range(bx - 1, bx + 2)
            for ny in range(by - 1, by + 2)
            for other_x, other_y in bins.get((nx, ny), [])
        )
        if too_close:
            continue
        bins[(bx, by)].append((x_value, y_value))
        accepted.append(
            {
                "x_physical": x_value,
                "y_physical": y_value,
                "omega": float(omega[i, j]),
                "lambda_ci": float(lambda_ci[i, j]),
                "rotation_sign": 1 if omega[i, j] >= 0.0 else -1,
            }
        )
        if len(accepted) >= maximum:
            break
    return accepted, {
        "lambda_ci_threshold": lambda_threshold,
        "absolute_vorticity_threshold": omega_threshold,
    }


def associate_cores(
    cores: list[dict],
    frame_index: int,
    active: dict[int, dict],
    next_id: int,
    config: dict,
) -> tuple[list[dict], dict[int, dict], int]:
    max_gap = int(config["maximum_track_gap_frames"])
    max_distance = float(config["maximum_reference_displacement"])
    eligible = {
        track_id: state
        for track_id, state in active.items()
        if frame_index - int(state["frame_index"]) <= max_gap + 1
    }
    candidates = []
    for core_index, core in enumerate(cores):
        for track_id, state in eligible.items():
            if int(core["rotation_sign"]) != int(state["rotation_sign"]):
                continue
            distance = math.hypot(
                float(core["x_physical"]) - float(state["x_physical"]),
                float(core["y_physical"]) - float(state["y_physical"]),
            )
            if distance <= max_distance:
                candidates.append((distance, core_index, track_id))
    assigned_cores: set[int] = set()
    assigned_tracks: set[int] = set()
    assignments: dict[int, int] = {}
    for _, core_index, track_id in sorted(candidates):
        if core_index in assigned_cores or track_id in assigned_tracks:
            continue
        assignments[core_index] = track_id
        assigned_cores.add(core_index)
        assigned_tracks.add(track_id)
    output = []
    updated = dict(eligible)
    for index, core in enumerate(cores):
        track_id = assignments.get(index)
        if track_id is None:
            track_id = next_id
            next_id += 1
        row = dict(core)
        row["reference_id"] = f"R{track_id:05d}"
        row["frame_index"] = frame_index
        updated[track_id] = dict(row)
        output.append(row)
    return output, updated, next_id


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def git_head(path: Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True, capture_output=True
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--mfc-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("results/stage5-manual"))
    parser.add_argument("--max-frames", type=int, default=0)
    args = parser.parse_args()

    import numpy as np

    root = Path(__file__).resolve().parents[1]
    config_path = args.config or root / "dart_stage5.json"
    config = json.loads(config_path.read_text())
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    case_dir = args.case_dir.resolve()
    mfc_root = args.mfc_root.resolve()
    if not case_dir.is_dir():
        parser.error(f"MFC case directory not found: {case_dir}")
    sys.path.insert(0, str(mfc_root / "toolchain"))
    from mfc.viz.reader import assemble, discover_timesteps

    available = discover_timesteps(str(case_dir), "binary")
    required = expected_steps(config)
    missing = sorted(set(required) - set(available))
    if missing:
        parser.error(
            f"raw MFC sequence is incomplete: missing {len(missing)} of {len(required)} expected steps; first missing={missing[0]}"
        )
    steps = required[: args.max_frames or None]
    all_rows: list[dict] = []
    active: dict[int, dict] = {}
    next_id = 1
    consistency = []
    per_frame = []
    sensitivity = {str(value): [] for value in config["sensitivity_quantiles"]}

    for frame_index, step in enumerate(steps):
        assembled = assemble(str(case_dir), step, fmt="binary")
        required_variables = {"vel1", "vel2", "omega3"}
        absent = sorted(required_variables - set(assembled.variables))
        if absent:
            raise RuntimeError(f"step {step} lacks variables: {absent}")
        xmask = (assembled.x_cc >= config["analysis_xlim"][0]) & (
            assembled.x_cc <= config["analysis_xlim"][1]
        )
        ymask = (assembled.y_cc >= config["analysis_ylim"][0]) & (
            assembled.y_cc <= config["analysis_ylim"][1]
        )
        x_indices = np.flatnonzero(xmask)
        y_indices = np.flatnonzero(ymask)
        if not x_indices.size or not y_indices.size:
            raise RuntimeError("Stage-5 crop does not overlap the MFC grid")
        x_indices = np.arange(max(0, x_indices[0] - 1), min(assembled.x_cc.size, x_indices[-1] + 2))
        y_indices = np.arange(max(0, y_indices[0] - 1), min(assembled.y_cc.size, y_indices[-1] + 2))
        x = assembled.x_cc[x_indices]
        y = assembled.y_cc[y_indices]
        vel1 = assembled.variables["vel1"][np.ix_(x_indices, y_indices)]
        vel2 = assembled.variables["vel2"][np.ix_(x_indices, y_indices)]
        omega_mfc = assembled.variables["omega3"][np.ix_(x_indices, y_indices)]
        fluid = geometry_fluid_mask(x, y)
        analysis_window = (
            (x[:, None] >= config["analysis_xlim"][0])
            & (x[:, None] <= config["analysis_xlim"][1])
            & (y[None, :] >= config["analysis_ylim"][0])
            & (y[None, :] <= config["analysis_ylim"][1])
        )
        fluid &= analysis_window
        finite = bool(
            np.isfinite(vel1[fluid]).all()
            and np.isfinite(vel2[fluid]).all()
            and np.isfinite(omega_mfc[fluid]).all()
        )
        if not finite:
            raise RuntimeError(f"non-finite raw field at step {step}")
        omega_derived, lambda_ci = velocity_diagnostics(x, y, vel1, vel2)
        corr = correlation(omega_mfc, omega_derived, fluid)
        if corr is not None:
            consistency.append(abs(corr))
        cores, thresholds = extract_cores(x, y, omega_mfc, lambda_ci, fluid, config)
        associated, active, next_id = associate_cores(
            cores, frame_index, active, next_id, config
        )
        for row in associated:
            row.update(
                {
                    "source_step": step,
                    "time": frame_index * float(config["snapshot_dt"]),
                }
            )
        all_rows.extend(associated)
        sensitivity_counts = {}
        for quantile in config["sensitivity_quantiles"]:
            variant, _ = extract_cores(
                x, y, omega_mfc, lambda_ci, fluid, config, float(quantile)
            )
            sensitivity[str(quantile)].append(len(variant))
            sensitivity_counts[str(quantile)] = len(variant)
        per_frame.append(
            {
                "frame_index": frame_index,
                "source_step": step,
                "time": frame_index * float(config["snapshot_dt"]),
                "reference_cores": len(associated),
                "vorticity_correlation": corr,
                **thresholds,
                "sensitivity_counts": sensitivity_counts,
            }
        )

    reference_path = output_dir / "stage5_reference.csv"
    write_csv(
        reference_path,
        all_rows,
        [
            "frame_index",
            "source_step",
            "time",
            "reference_id",
            "x_physical",
            "y_physical",
            "rotation_sign",
            "omega",
            "lambda_ci",
        ],
    )
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in all_rows:
        grouped[row["reference_id"]].append(row)
    track_rows = []
    for reference_id, rows in sorted(grouped.items()):
        rows.sort(key=lambda item: item["frame_index"])
        first, last = rows[0], rows[-1]
        track_rows.append(
            {
                "reference_id": reference_id,
                "observations": len(rows),
                "first_frame": first["frame_index"],
                "last_frame": last["frame_index"],
                "lifetime": (last["frame_index"] - first["frame_index"])
                * float(config["snapshot_dt"]),
                "displacement": math.hypot(
                    last["x_physical"] - first["x_physical"],
                    last["y_physical"] - first["y_physical"],
                ),
                "rotation_sign": first["rotation_sign"],
            }
        )
    write_csv(
        output_dir / "stage5_reference_tracks.csv",
        track_rows,
        [
            "reference_id",
            "observations",
            "first_frame",
            "last_frame",
            "lifetime",
            "displacement",
            "rotation_sign",
        ],
    )

    consistency_pass, consistency_summary = assess_vorticity_consistency(
        per_frame, config
    )
    reference_pass = len(all_rows) >= int(config["minimum_reference_rows"])
    gate_pass = consistency_pass and reference_pass and len(steps) == len(required)
    report = {
        "schema_version": 1,
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": config["case_id"],
        "project_commit": git_head(root.parents[1]),
        "mfc_commit": git_head(mfc_root),
        "case_dir": str(case_dir),
        "frames": len(steps),
        "steps": steps,
        "reference_rows": len(all_rows),
        "reference_tracks": len(track_rows),
        "vorticity_consistency_frames": len(consistency),
        "vorticity_consistency": consistency_summary,
        "minimum_absolute_vorticity_correlation": min(consistency) if consistency else None,
        "median_absolute_vorticity_correlation": statistics.median(consistency) if consistency else None,
        "per_frame": per_frame,
        "sensitivity_core_counts": sensitivity,
        "gates": {
            "raw_sequence_complete": "pass" if len(steps) == len(required) else "fail",
            "finite_fields": "pass",
            "derived_vorticity_consistency": "pass" if consistency_pass else "fail",
            "reference_catalogue": "pass" if reference_pass else "fail",
        },
        "claim_gate": (
            "raw_field_reference_ready_for_dart_comparison"
            if gate_pass
            else "raw_field_reference_gate_failed"
        ),
        "reference_csv": str(reference_path),
        "limitations": [
            "Threshold sensitivity must be reported; one quantile pair is not a unique vortex definition.",
            "This two-dimensional reference uses lambda_ci and signed omega_z; it is not a three-dimensional vortex tube criterion.",
            "DART publication claims require a subsequent independent match against this catalogue.",
        ],
    }
    report_path = output_dir / "stage5_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print("STAGE5_STATUS=completed")
    print(f"STAGE5_FRAMES={len(steps)}")
    print(f"STAGE5_REFERENCE_ROWS={len(all_rows)}")
    print(f"STAGE5_REFERENCE_TRACKS={len(track_rows)}")
    print(f"STAGE5_CLAIM_GATE={report['claim_gate']}")
    print(f"STAGE5_REFERENCE_CSV={reference_path}")
    print(f"STAGE5_REPORT={report_path}")
    return 0 if gate_pass else 5


if __name__ == "__main__":
    raise SystemExit(main())
