#!/usr/bin/env python3
"""Write deterministic hashes and dimensions for the committed pilot inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    inputs = sorted((root / "inputs").rglob("*.png"))
    manifest = []
    for path in inputs:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with Image.open(path) as image:
            width, height = image.size
        manifest.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": digest,
                "width": width,
                "height": height,
                "bytes": path.stat().st_size,
            }
        )
    (root / "results" / "input_manifest.json").write_text(
        json.dumps({"schema_version": 1, "inputs": manifest}, indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
