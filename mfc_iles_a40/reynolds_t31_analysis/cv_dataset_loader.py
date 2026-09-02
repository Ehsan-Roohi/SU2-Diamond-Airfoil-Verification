#!/usr/bin/env python3
"""Minimal NumPy loader for the exported MFC vision dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import numpy as np


class MFCCVDataset:
    """Index manifest rows and load one NPZ sample at a time.

    Arrays follow image convention: fields are ``(channels, height, width)``
    and masks are ``(height, width)``. Guard frames are excluded by default.
    """

    def __init__(
        self,
        root: str | Path,
        split: str | None = None,
        *,
        normalize: bool = False,
    ) -> None:
        self.root = Path(root).resolve()
        manifest = self.root / "manifest.jsonl"
        rows = [
            json.loads(line)
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if split is None:
            rows = [row for row in rows if row["split"] != "guard"]
        else:
            if split not in {"train", "val", "test", "guard"}:
                raise ValueError(f"unknown split: {split}")
            rows = [row for row in rows if row["split"] == split]
        self.rows = rows
        self.normalize = normalize
        self.normalization: dict[str, Any] | None = None
        if normalize:
            self.normalization = json.loads(
                (self.root / "normalization.json").read_text(encoding="utf-8")
            )

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        metadata = self.rows[index]
        with np.load(self.root / metadata["tensor"], allow_pickle=False) as source:
            arrays = {name: source[name] for name in source.files}
        if self.normalization is not None:
            names = [str(value) for value in arrays["field_names"]]
            channels = self.normalization["channels"]
            mean = np.asarray([channels[name]["mean"] for name in names], dtype=np.float32)
            std = np.asarray([channels[name]["std"] for name in names], dtype=np.float32)
            std = np.maximum(std, np.float32(1.0e-12))
            fields = (np.asarray(arrays["fields"], dtype=np.float32) - mean[:, None, None]) / std[:, None, None]
            valid = np.asarray(arrays["label_valid_mask"], dtype=bool)
            fields[:, ~valid] = 0.0
            arrays["fields"] = fields
        return {"metadata": metadata, **arrays}

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for index in range(len(self)):
            yield self[index]
