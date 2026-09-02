#!/usr/bin/env python3
"""Analyze a pruned initial run from native loads plus its final raw field.

The dense ``ib_state`` history is only 160 bytes per snapshot and was retained
even when dense fluid checkpoints were deleted.  It is authoritative for lift
and drag.  The sole retained t=6 fluid checkpoint supports final-field and
bow-shock diagnostics, but it cannot support a time-resolved shock history.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

from raw_restart_reader import assemble_raw, discover_raw_steps


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--mfc-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dt", type=float, required=True)
    parser.add_argument("--analysis-start", type=float, default=3.0)
    parser.add_argument("--alpha", type=float, default=40.0)
    parser.add_argument("--rho-inf", type=float, default=1.0)
    parser.add_argument("--u-inf", type=float, default=3.0)
    parser.add_argument("--chord", type=float, default=1.0)
    parser.add_argument("--reynolds", type=float, default=1.0e6)
    args = parser.parse_args()
    case = args.case_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
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
    direct = article.read_ib_state_history(case, ref)
    direct = [
        row
        for row in direct
        if all(
            math.isfinite(float(row[key]))
            for key in ("time", "force_x", "force_y", "CL", "CD")
        )
    ]
    direct.sort(key=lambda row: int(row["step"]))
    if len(direct) < 32 or any(
        int(right["step"]) <= int(left["step"])
        for left, right in zip(direct, direct[1:])
    ):
        raise RuntimeError("native IB load history is incomplete or unordered")
    force_rows = [
        {
            "step": int(row["step"]),
            "time": int(row["step"]) * args.dt,
            "force_x": float(row["force_x"]),
            "force_y": float(row["force_y"]),
            "CD": float(row["CD"]),
            "CL": float(row["CL"]),
            "force_source": "NATIVE_IB_STATE",
        }
        for row in direct
    ]
    active = [
        row for row in force_rows if row["time"] >= args.analysis_start - 1.0e-9
    ]
    if len(active) < 16 or active[-1]["time"] < 6.0 - args.dt:
        raise RuntimeError("native IB loads do not cover the requested t=3..6 window")
    raw_steps = discover_raw_steps(case)
    if not raw_steps or raw_steps[-1] * args.dt < 6.0 - args.dt:
        raise RuntimeError("the retained raw field does not reach t=6")
    final_step = raw_steps[-1]
    _, shock_rows, field_info = article.snapshot_rows(
        case,
        [final_step],
        args.dt,
        ref,
        direct,
        lambda directory, step: assemble_raw(
            directory, step, crop=(-1.5, 2.0, -1.5, 2.0), halo=6
        ),
        "raw_restart_mpiio_final_only",
    )
    article.write_csv(output / "mfc_hll_force_history.csv", force_rows)
    article.write_csv(output / "mfc_hll_shock_history.csv", shock_rows)
    force_statistics = {
        key: article.correlated_statistics(
            np.asarray([row[key] for row in active], dtype=float)
        )
        for key in ("CL", "CD")
    }
    times = np.asarray([row["time"] for row in active], dtype=float)
    lift = np.asarray([row["CL"] for row in active], dtype=float)
    shedding, frequency, power = article.spectral_metrics(times, lift, ref)
    if frequency.size:
        article.write_csv(
            output / "mfc_hll_lift_spectrum.csv",
            [
                {
                    "frequency": float(value),
                    "strouhal": float(value * ref.chord / ref.u_inf),
                    "power": float(level),
                }
                for value, level in zip(frequency, power)
            ],
        )
    detected = [
        row for row in shock_rows if row.get("stand_off_over_c") is not None
    ]
    shock_statistics: dict[str, object] = {
        "detected_samples": len(detected),
        "requested_samples": 1,
        "qualification": "FINAL_FIELD_ONLY_DUE_TO_INTENTIONAL_PRUNING",
    }
    if detected:
        for key in ("stand_off_over_c", "shock_angle_to_freestream_deg"):
            shock_statistics[key] = article.correlated_statistics(
                np.asarray([float(row[key]) for row in detected], dtype=float)
            )
    article.save_plots(
        output,
        force_rows,
        shock_rows,
        frequency,
        power,
        field_info["final_plot"],
        args.analysis_start,
    )
    summary = {
        "status": "PRELIMINARY_PRUNED_FIELD_HISTORY",
        "method": "MFC viscous ILES/no-model, WENO5-unmapped HLL",
        "case_dir": str(case),
        "alpha_deg": args.alpha,
        "Mach_inf": args.u_inf,
        "Re_c": args.reynolds,
        "dt": args.dt,
        "field_format": "NATIVE_IB_HISTORY_PLUS_FINAL_RAW_FIELD",
        "saved_steps": [int(row["step"]) for row in force_rows],
        "statistical_window": [args.analysis_start, active[-1]["time"]],
        "force_source": "NATIVE_IB_STATE",
        "force_source_assessment": "AUTHORITATIVE_NATIVE_LOADS",
        "field_force_validation": field_info["validation"],
        "force_statistics": force_statistics,
        "shedding": shedding,
        "shock_statistics": shock_statistics,
        "comparison": [],
        "input_provenance": {
            "native_ib_records": len(direct),
            "retained_raw_fields": raw_steps,
            "shock_history_limitation": "only the final retained field was measurable",
        },
    }
    (output / "mfc_hll_article_metrics.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "ARTICLE_SUMMARY.txt").write_text(
        "MFC PRUNED INITIAL-RUN DIAGNOSTICS\n"
        "==================================\n"
        "force_history=NATIVE_IB_STATE\n"
        "shock_history=FINAL_RETAINED_FIELD_ONLY\n"
        "dense_fluid_history=INTENTIONALLY_PRUNED\n",
        encoding="utf-8",
    )
    print(
        f"PRUNED_INITIAL_ANALYSIS=PASS native_loads={len(direct)} "
        f"final_step={final_step}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
