#!/usr/bin/env python3
"""Summarize an SU2 history without implying that a run is accepted."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("history", nargs="?", default="history_second_order.csv")
    parser.add_argument("--window", type=int, default=200)
    parser.add_argument(
        "--alpha", type=float,
        help="angle of attack; at alpha=0 CL stability is reported as absolute p-to-p",
    )
    return parser.parse_args()


def compact(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def find_column(fields: list[str], exact: tuple[str, ...]) -> str | None:
    lookup = {compact(field): field for field in fields}
    for candidate in exact:
        if compact(candidate) in lookup:
            return lookup[compact(candidate)]
    for clean, original in lookup.items():
        if any(compact(candidate) in clean for candidate in exact):
            return original
    return None


def values(rows: list[dict[str, str]], key: str | None) -> list[float]:
    if key is None:
        return []
    result = []
    for row in rows:
        try:
            result.append(float(row[key]))
        except (KeyError, TypeError, ValueError):
            pass
    return result


def main() -> int:
    args = parse_args()
    path = Path(args.history)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit(f"No data rows in {path}")
    fields = list(rows[0])
    mapping = {
        "density residual": find_column(fields, ("RMS_DENSITY", "RMS[RHO]", "RMSRHO")),
        "CL": find_column(fields, ("LIFT", "CL")),
        "CD": find_column(fields, ("DRAG", "CD")),
    }
    print(f"History: {path}")
    print(f"Rows: {len(rows)}; requested final window: {args.window}")
    for label, key in mapping.items():
        series = values(rows, key)
        if not series:
            print(f"{label}: column not found")
            continue
        sample = series[-min(args.window, len(series)):]
        mean = sum(sample) / len(sample)
        peak_to_peak = max(sample) - min(sample)
        if label in ("CL", "CD"):
            if label == "CL" and args.alpha is not None and abs(args.alpha) < 1.0e-12:
                print(
                    f"{label}: final={sample[-1]:.8g}, mean={mean:.8g}, "
                    f"absolute peak-to-peak={peak_to_peak:.3g} "
                    "(zero-incidence acceptance must use an absolute limit)"
                )
            else:
                relative = 100.0 * peak_to_peak / max(abs(mean), 1.0e-300)
                print(
                    f"{label}: final={sample[-1]:.8g}, mean={mean:.8g}, "
                    f"peak-to-peak={peak_to_peak:.3g} ({relative:.3g}%)"
                )
        else:
            print(f"{label}: initial={series[0]:.8g}, final={series[-1]:.8g}")
    print("Diagnostic summary only; use run_case.py for acceptance checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
