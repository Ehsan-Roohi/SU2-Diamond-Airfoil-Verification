#!/usr/bin/env python3
"""Merge validated t=6..21 diagnostics with retained raw fields to t=31."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

from raw_restart_reader import assemble_raw, discover_raw_steps


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def numeric(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_rows(
    rows: list[dict[str, Any]], dt: float, *, require_forces: bool
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in rows:
        row: dict[str, Any] = dict(source)
        time = numeric(row.get("time"))
        if time is None:
            continue
        step_value = numeric(row.get("step"))
        row["step"] = int(round(time / dt) if step_value is None else step_value)
        row["time"] = time
        for key, value in list(row.items()):
            converted = numeric(value)
            # Preserve categorical provenance, but normalize blank/NaN numeric
            # cells to None so the downstream plotting code cannot mistake an
            # empty CSV cell for a detected shock.
            if converted is not None:
                row[key] = converted
            elif key in {
                "step",
                "time",
                "CL",
                "CD",
                "CL_pressure",
                "CD_pressure",
                "CL_viscous",
                "CD_viscous",
                "stand_off_over_c",
                "shock_angle_to_freestream_deg",
            }:
                row[key] = None
        if require_forces and not all(
            numeric(row.get(key)) is not None for key in ("CL", "CD")
        ):
            raise RuntimeError("prior force history contains non-finite CL/CD")
        result.append(row)
    result.sort(key=lambda row: float(row["time"]))
    return result


def merge_by_step(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = {int(row["step"]): row for row in first}
    for row in second:
        merged[int(row["step"])] = row
    return [merged[key] for key in sorted(merged)]


def finite_values(rows: list[dict[str, Any]], key: str) -> np.ndarray:
    values = [numeric(row.get(key)) for row in rows]
    return np.asarray([value for value in values if value is not None], dtype=float)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--mfc-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dt", type=float, required=True)
    parser.add_argument("--analysis-start", type=float, default=6.0)
    parser.add_argument("--alpha", type=float, default=40.0)
    parser.add_argument("--rho-inf", type=float, default=1.0)
    parser.add_argument("--u-inf", type=float, default=3.0)
    parser.add_argument("--chord", type=float, default=1.0)
    parser.add_argument("--reynolds", type=float, default=1.0e6)
    args = parser.parse_args()

    case_dir = args.case_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = case_dir / "long_view_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = manifest["selected_diagnostics"]
    prior_force_path = Path(selected["force"]["path"])
    prior_shock_path = Path(selected["shock"]["path"])
    for kind, path in (("force", prior_force_path), ("shock", prior_shock_path)):
        expected = selected[kind].get("sha256")
        if not expected or digest(path) != expected:
            raise RuntimeError(f"validated {kind} history changed after preflight: {path}")
    prior_force = normalize_rows(
        read_rows(prior_force_path), args.dt, require_forces=True
    )
    prior_shock = normalize_rows(
        read_rows(prior_shock_path), args.dt, require_forces=False
    )
    prior_end = min(
        max(float(row["time"]) for row in prior_force),
        max(float(row["time"]) for row in prior_shock),
    )
    if prior_end < 20.95:
        raise RuntimeError(
            f"validated dense history ends at t={prior_end:g}; expected coverage through t=21"
        )
    prior_force = [
        row
        for row in prior_force
        if args.analysis_start - 1.0e-9 <= float(row["time"]) <= prior_end + 1.0e-9
    ]
    prior_shock = [
        row
        for row in prior_shock
        if args.analysis_start - 1.0e-9 <= float(row["time"]) <= prior_end + 1.0e-9
    ]

    analyzer_dir = Path(__file__).resolve().parents[1] / "hll_production_analysis"
    sys.path.insert(0, str(analyzer_dir))
    import analyze_mfc_hll_article as article

    ref = article.FlowReference(
        alpha_deg=args.alpha,
        rho_inf=args.rho_inf,
        u_inf=args.u_inf,
        chord=args.chord,
        reynolds=args.reynolds,
    )
    raw_steps = [
        step
        for step in discover_raw_steps(case_dir)
        if step * args.dt > prior_end + 1.0e-9
    ]
    if not raw_steps or not math.isclose(
        raw_steps[-1] * args.dt, 31.0, abs_tol=1.0e-8
    ):
        raise RuntimeError("retained raw continuation does not reach t=31")
    direct_rows = article.read_ib_state_history(case_dir, ref)
    raw_force, raw_shock, field_info = article.snapshot_rows(
        case_dir,
        raw_steps,
        args.dt,
        ref,
        direct_rows,
        lambda directory, step: assemble_raw(
            directory, step, crop=(-1.5, 2.0, -1.5, 2.0), halo=6
        ),
        "raw_restart_mpiio",
    )
    force_rows = merge_by_step(prior_force, raw_force)
    shock_rows = merge_by_step(prior_shock, raw_shock)
    article.write_csv(output / "mfc_hll_force_history.csv", force_rows)
    article.write_csv(output / "mfc_hll_shock_history.csv", shock_rows)

    force_statistics: dict[str, Any] = {}
    for key in (
        "CL",
        "CD",
        "CL_pressure",
        "CD_pressure",
        "CL_viscous",
        "CD_viscous",
    ):
        values = finite_values(force_rows, key)
        if values.size >= 16:
            force_statistics[key] = article.correlated_statistics(values)

    # Only the unpruned t=26..31 tail has the uniform sampling needed for an
    # honest spectrum. The sparse t=21..26 retention is never interpolated.
    spectral_rows = [
        row
        for row in raw_force
        if float(row["time"]) >= 26.0 - 1.0e-9
    ]
    spectral_time = np.asarray(
        [float(row["time"]) for row in spectral_rows], dtype=float
    )
    spectral_cl = np.asarray(
        [float(row["CL"]) for row in spectral_rows], dtype=float
    )
    shedding, frequency, psd = article.spectral_metrics(
        spectral_time, spectral_cl, ref
    )
    shedding["source_window"] = [26.0, 31.0]
    shedding["sampling_note"] = (
        "computed only from the uniform retained t=26..31 raw record"
    )
    if frequency.size:
        article.write_csv(
            output / "mfc_hll_lift_spectrum.csv",
            [
                {
                    "frequency": float(freq),
                    "strouhal": float(freq * ref.chord / ref.u_inf),
                    "power": float(power),
                }
                for freq, power in zip(frequency, psd)
            ],
        )

    detected = [
        row
        for row in shock_rows
        if numeric(row.get("stand_off_over_c")) is not None
    ]
    shock_statistics: dict[str, Any] = {
        "detected_samples": len(detected),
        "requested_samples": len(shock_rows),
    }
    if detected:
        for key in (
            "stand_off_over_c",
            "shock_angle_to_freestream_deg",
        ):
            values = finite_values(detected, key)
            if values.size:
                shock_statistics[key] = article.correlated_statistics(values)

    article.save_plots(
        output,
        force_rows,
        shock_rows,
        frequency,
        psd,
        field_info["final_plot"],
        args.analysis_start,
    )
    saved_steps = [int(row["step"]) for row in force_rows]
    summary = {
        "status": "PRELIMINARY_HYBRID_RETENTION",
        "method": "MFC viscous ILES/no-model, WENO5-unmapped HLL",
        "case_dir": str(case_dir),
        "alpha_deg": args.alpha,
        "Mach_inf": args.u_inf,
        "Re_c": args.reynolds,
        "dt": args.dt,
        "field_format": "HYBRID_DERIVED_T06_T21_PLUS_RAW_T21_T31",
        "saved_steps": saved_steps,
        "statistical_window": [args.analysis_start, 31.0],
        "force_source": "HYBRID_VALIDATED_DERIVED_AND_FIELD_RECONSTRUCTION",
        "force_source_assessment": (
            "PROVISIONAL; t=21..26 is intentionally sparse and is not interpolated"
        ),
        "field_force_validation": field_info["validation"],
        "force_statistics": force_statistics,
        "shedding": shedding,
        "shock_statistics": shock_statistics,
        "comparison": [],
        "input_provenance": {
            "prior_force_history": str(prior_force_path.resolve()),
            "prior_shock_history": str(prior_shock_path.resolve()),
            "prior_dense_end": prior_end,
            "retained_raw_steps_after_prior": raw_steps,
            "manifest": str(manifest_path.resolve()),
        },
    }
    (output / "mfc_hll_article_metrics.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "ARTICLE_SUMMARY.txt").write_text(
        "MFC HLL LONG-CHAIN DIAGNOSTICS\n"
        "================================\n"
        "status=PRELIMINARY_HYBRID_RETENTION\n"
        f"prior_dense_window={args.analysis_start:g}..{prior_end:g}\n"
        f"retained_raw_samples_after_prior={len(raw_steps)}\n"
        "spectrum_window=26..31\n"
        "t21..26_sparse_retention=NO_INTERPOLATION\n",
        encoding="utf-8",
    )
    print(
        f"LONG_CHAIN_ANALYSIS=PASS prior_to={prior_end:g} "
        f"raw_samples={len(raw_steps)} final_time=31"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
