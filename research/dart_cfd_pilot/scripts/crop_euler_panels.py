#!/usr/bin/env python3
"""Extract clean MFC panels from the archived four-panel field figure."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


CROPS = {
    "mach": (240, 55, 1390, 875),
    "schlieren": (1740, 935, 2800, 1775),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    image = Image.open(args.source).convert("RGB")
    if image.size != (2904, 1803):
        raise ValueError(f"unexpected source size {image.size}; expected (2904, 1803)")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, box in CROPS.items():
        image.crop(box).save(args.output_dir / f"euler_mfc_alpha40_{name}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
