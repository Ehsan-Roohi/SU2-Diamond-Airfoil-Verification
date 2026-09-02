#!/usr/bin/env python3
"""Select complete field evidence or reuse validated MFC diagnostics.

Most Reynolds-screening cases still contain every raw restart field and are
reanalyzed by the current workflow.  The original Re=1e6 run was deliberately
pruned after its article diagnostics were produced.  This helper validates
those existing diagnostics and copies only the small, machine-readable
products into the new analysis directory.  It never treats a completion
marker or a final checkpoint as a time history.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

from raw_restart_reader import discover_raw_steps


STANDARD_FILES = (
    "mfc_hll_article_metrics.json",
    "mfc_hll_force_history.csv",
    "mfc_hll_shock_history.csv",
)


def binary_steps(case_dir: Path) -> list[int]:
    """Discover root-assembled or rank-local post-processed snapshots."""

    for directory in (
        case_dir / "binary" / "root",
        case_dir / "binary" / "p0",
    ):
        if not directory.is_dir():
            continue
        steps = sorted(
            int(path.stem)
            for path in directory.glob("[0-9]*.dat")
            if path.stem.isdigit()
            and path.is_file()
            and path.stat().st_size > 0
        )
        if steps:
            return steps
    return []


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def validate_diagnostic(directory: Path, dt: float, analysis_start: float) -> dict[str, Any]:
    paths = {name: directory / name for name in STANDARD_FILES}
    if not all(path.is_file() and path.stat().st_size > 0 for path in paths.values()):
        raise RuntimeError("standard diagnostic files are incomplete")
    payload = json.loads(paths["mfc_hll_article_metrics.json"].read_text(encoding="utf-8"))
    force = read_csv(paths["mfc_hll_force_history.csv"])
    shock = read_csv(paths["mfc_hll_shock_history.csv"])
    active = [
        row
        for row in force
        if finite(row.get("time")) is not None
        and float(row["time"]) >= analysis_start - 1.0e-9
        and finite(row.get("CL")) is not None
        and finite(row.get("CD")) is not None
    ]
    shock_active = [
        row
        for row in shock
        if finite(row.get("time")) is not None
        and float(row["time"]) >= analysis_start - 1.0e-9
        and finite(row.get("stand_off_over_c")) is not None
    ]
    times = [float(row["time"]) for row in active]
    if len(active) < 16:
        raise RuntimeError(f"only {len(active)} finite force samples in statistical window")
    if not times or max(times) < 6.0 - max(dt, 1.0e-8):
        raise RuntimeError("force history does not reach t=6")
    if len(shock_active) < 4:
        raise RuntimeError(f"only {len(shock_active)} finite shock samples in statistical window")
    for coefficient in ("CL", "CD"):
        stats = payload.get("force_statistics", {}).get(coefficient, {})
        if int(stats.get("samples", 0)) < 16:
            raise RuntimeError(f"metrics lack a valid {coefficient} statistical window")
        for key in ("mean", "rms_fluctuation", "ci95_mean"):
            if finite(stats.get(key)) is None:
                raise RuntimeError(f"metrics contain non-finite {coefficient}.{key}")
    return {
        "directory": str(directory.resolve()),
        "force_rows": len(force),
        "active_force_rows": len(active),
        "active_shock_rows": len(shock_active),
        "time_start": min(times),
        "time_end": max(times),
        "mtime": paths["mfc_hll_article_metrics.json"].stat().st_mtime,
    }


def diagnostic_candidates(case_dir: Path, dt: float, analysis_start: float) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for metrics in case_dir.rglob("mfc_hll_article_metrics.json"):
        if not metrics.is_file():
            continue
        try:
            candidates.append(validate_diagnostic(metrics.parent, dt, analysis_start))
        except (OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError):
            continue
    candidates.sort(
        key=lambda row: (
            float(row["time_end"]),
            int(row["active_force_rows"]),
            float(row["mtime"]),
        ),
        reverse=True,
    )
    return candidates


def select_evidence(case_dir: Path, dt: float, analysis_start: float) -> dict[str, Any]:
    raw = discover_raw_steps(case_dir)
    binary = binary_steps(case_dir)
    usable = raw if len(raw) >= len(binary) and raw else binary
    source_format = "raw_restart_mpiio" if usable is raw else "binary_post_process"
    active = [step for step in usable if step * dt >= analysis_start - 1.0e-9]
    if len(active) >= 16 and usable[-1] * dt >= 6.0 - max(dt, 1.0e-8):
        return {
            "mode": "ANALYZE_FIELDS",
            "field_format": source_format,
            "field_snapshots": len(usable),
            "active_snapshots": len(active),
            "first_step": usable[0],
            "last_step": usable[-1],
        }
    reusable = diagnostic_candidates(case_dir, dt, analysis_start)
    if reusable:
        return {
            "mode": "REUSE_VALIDATED_DIAGNOSTICS",
            "field_format": source_format if usable else "NONE",
            "field_snapshots": len(usable),
            "active_snapshots": len(active),
            "diagnostic": reusable[0],
        }
    ib_steps: list[int] = []
    for path in (case_dir / "restart_data").glob("ib_state_[0-9]*.dat"):
        stem = path.stem.removeprefix("ib_state_")
        if stem.isdigit() and path.is_file() and path.stat().st_size == 160:
            values = struct.unpack("=20d", path.read_bytes())
            if all(math.isfinite(value) for value in values[:3]):
                ib_steps.append(int(stem))
    ib_steps.sort()
    active_ib = [step for step in ib_steps if step * dt >= analysis_start - 1.0e-9]
    if (
        len(active_ib) >= 16
        and ib_steps
        and ib_steps[-1] * dt >= 6.0 - max(dt, 1.0e-8)
        and raw
        and raw[-1] * dt >= 6.0 - max(dt, 1.0e-8)
    ):
        return {
            "mode": "NATIVE_LOADS_PLUS_FINAL_FIELD",
            "field_format": "raw_restart_mpiio_final_only",
            "field_snapshots": len(raw),
            "native_ib_snapshots": len(ib_steps),
            "active_native_ib_snapshots": len(active_ib),
            "first_step": ib_steps[0],
            "last_step": ib_steps[-1],
        }
    raise RuntimeError(
        f"{case_dir} has only {len(usable)} usable fields ({len(active)} after "
        f"t={analysis_start:g}) and no validated reusable diagnostics"
    )


def copy_diagnostics(source: Path, output: Path, evidence: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    names = set(STANDARD_FILES)
    names.update(
        path.name
        for path in source.iterdir()
        if path.is_file()
        and (
            path.name.startswith("mfc_hll_")
            or path.name in {"ARTICLE_SUMMARY.txt", "article_solver_comparison.csv"}
        )
    )
    for name in sorted(names):
        path = source / name
        if path.is_file() and path.stat().st_size > 0:
            shutil.copy2(path, output / name)
    (output / "diagnostic_source.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--dt", type=float, required=True)
    parser.add_argument("--analysis-start", type=float, required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--analyzer", type=Path)
    parser.add_argument("--pruned-analyzer", type=Path)
    parser.add_argument("--mfc-root", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--alpha", type=float, default=40.0)
    parser.add_argument("--rho-inf", type=float, default=1.0)
    parser.add_argument("--u-inf", type=float, default=3.0)
    parser.add_argument("--chord", type=float, default=1.0)
    parser.add_argument("--reynolds", type=float, default=1.0e6)
    args = parser.parse_args()
    case_dir = args.case_dir.resolve()
    evidence = select_evidence(case_dir, args.dt, args.analysis_start)
    if args.check_only:
        print(json.dumps(evidence, sort_keys=True, allow_nan=False))
        return 0
    if args.output_dir is None:
        parser.error("--output-dir is required unless --check-only is used")
    output = args.output_dir.resolve()
    if evidence["mode"] == "REUSE_VALIDATED_DIAGNOSTICS":
        source = Path(evidence["diagnostic"]["directory"])
        payload = json.loads(
            (source / "mfc_hll_article_metrics.json").read_text(encoding="utf-8")
        )
        recorded_re = finite(payload.get("Re_c"))
        recorded_alpha = finite(payload.get("alpha_deg"))
        if recorded_re is not None and not math.isclose(
            recorded_re, args.reynolds, rel_tol=1.0e-9
        ):
            raise RuntimeError(
                f"reusable diagnostic Re_c={recorded_re:g}, expected {args.reynolds:g}"
            )
        if recorded_alpha is not None and not math.isclose(
            recorded_alpha, args.alpha, abs_tol=0.05
        ):
            raise RuntimeError(
                f"reusable diagnostic alpha={recorded_alpha:g}, expected {args.alpha:g}"
            )
        copy_diagnostics(source, output, evidence)
    else:
        if args.analyzer is None or args.mfc_root is None:
            parser.error("--analyzer and --mfc-root are required for field analysis")
        program = (
            args.pruned_analyzer
            if evidence["mode"] == "NATIVE_LOADS_PLUS_FINAL_FIELD"
            else args.analyzer
        )
        if program is None:
            parser.error("--pruned-analyzer is required for a pruned field history")
        command = [
            sys.executable,
            str(program.resolve()),
            str(case_dir),
            "--mfc-root", str(args.mfc_root.resolve()),
            "--output-dir", str(output),
            "--dt", str(args.dt),
            "--analysis-start", str(args.analysis_start),
            "--alpha", str(args.alpha),
            "--rho-inf", str(args.rho_inf),
            "--u-inf", str(args.u_inf),
            "--chord", str(args.chord),
            "--reynolds", str(args.reynolds),
        ]
        if evidence["mode"] == "ANALYZE_FIELDS":
            command.extend(
                [
                    "--field-format",
                    "binary"
                    if evidence["field_format"] == "binary_post_process"
                    else "raw",
                ]
            )
        output.mkdir(parents=True, exist_ok=True)
        with (output / "analyze.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True)
        if completed.returncode:
            raise RuntimeError(f"field analyzer returned {completed.returncode}; see {output / 'analyze.log'}")
        (output / "diagnostic_source.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    for name in STANDARD_FILES:
        path = output / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"missing standardized output: {path}")
    print(f"CASE_EVIDENCE=PASS mode={evidence['mode']} case={case_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
