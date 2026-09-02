#!/usr/bin/env python3
"""Create a provenance-preserving high-viscosity variant of the HLL case."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


EXPECTED_SOURCE_SHA256 = "f189828883d7d0c1ccc523868e1171ccd63c11af8cc4ce027eaf3003ee49236d"
SOURCE_LINE = "re_chord = 1.0e6"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--re-chord", required=True, type=float)
    args = parser.parse_args()

    if not (0.0 < args.re_chord < 1.0e6):
        parser.error("--re-chord must be positive and below the Re=1e6 baseline")
    raw = args.source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SOURCE_SHA256:
        raise SystemExit(f"ERROR: source case hash mismatch: {digest}")
    text = raw.decode("utf-8")
    if text.count(SOURCE_LINE) != 1:
        raise SystemExit("ERROR: expected exactly one baseline Reynolds-number assignment")
    replacement = f"re_chord = {args.re_chord:.17g}  # Tim Colonius viscosity ladder"
    args.output.write_text(text.replace(SOURCE_LINE, replacement), encoding="utf-8")
    print(f"VISCOSITY_LADDER_CASE=PASS Re_c={args.re_chord:.12g} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
