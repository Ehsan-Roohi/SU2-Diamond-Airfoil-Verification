#!/usr/bin/env python3
"""Materialize a pinned MFC diamond-airfoil case at a new incidence angle."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


ANGLE_ASSIGNMENT = "alpha_deg = 40.0"


def materialize(source: str, alpha_deg: float) -> str:
    if source.count(ANGLE_ASSIGNMENT) != 1:
        raise ValueError(
            f"expected exactly one pinned assignment {ANGLE_ASSIGNMENT!r}; "
            f"found {source.count(ANGLE_ASSIGNMENT)}"
        )
    rendered = source.replace(
        "Post-process and restart the MFC A40 viscous/no-model screen safely.",
        "Cross-case MFC viscous/no-model diamond-airfoil run.",
    )
    rendered = rendered.replace(ANGLE_ASSIGNMENT, f"alpha_deg = {float(alpha_deg)!r}")
    header = (
        "# Generated reproducibly by materialize_mfc_cross_case.py; "
        f"alpha_deg={float(alpha_deg):.12g}\n"
    )
    return header + rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--alpha-deg", type=float, required=True)
    args = parser.parse_args()
    if not 0.0 < args.alpha_deg < 90.0:
        parser.error("--alpha-deg must be between 0 and 90 degrees")
    text = materialize(args.source.read_text(), args.alpha_deg)
    args.output.write_text(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    print(f"MFC_CROSS_CASE_ALPHA_DEG={args.alpha_deg:.12g}")
    print(f"MFC_CROSS_CASE_SHA256={digest}")
    print(f"MFC_CROSS_CASE_OUTPUT={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
