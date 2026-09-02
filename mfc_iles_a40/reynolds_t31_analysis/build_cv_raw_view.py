#!/usr/bin/env python3
"""Build a symlink-only view of retained Re=1e6 fields for CV export.

This deliberately has no dependency on force histories, movie evidence, or
the article-analysis long-view audit. At duplicated restart steps the final
checkpoint from the preceding stage is selected because it is the canonical
restart source; later stages may rewrite their copied start-step output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from raw_restart_reader import discover_raw_fields


DT = 1.0 / 5400.0
SAVE_STEPS = 270
STAGES = (
    ("t06_t11", 6.0, 11.0),
    ("t11_t16", 11.0, 16.0),
    ("t16_t21", 16.0, 21.0),
    ("t21_t26", 21.0, 26.0),
    ("t26_t31", 26.0, 31.0),
)


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def marker(path: Path, expected_start: int | None = None) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"missing solver completion marker: {path}")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    if values.get("status") != "PASS":
        raise RuntimeError(f"solver marker is not PASS: {path}")
    if expected_start is not None and values.get("start_step") != str(expected_start):
        raise RuntimeError(
            f"wrong start_step in {path}: {values.get('start_step')!r}; "
            f"expected {expected_start}"
        )
    return values


def link(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        if target.resolve() == source.resolve():
            return
        raise RuntimeError(f"refusing to replace existing CV-view entry: {target}")
    target.symlink_to(source.resolve())


def build(initial: Path, chain: Path, output: Path) -> dict[str, object]:
    initial = initial.resolve()
    chain = chain.resolve()
    output = output.resolve()
    marker(initial / "RUN_OK_INITIAL.txt")
    sources: list[tuple[str, float, float, Path, Path]] = [
        ("t00_t06", 0.0, 6.0, initial, initial / "RUN_OK_INITIAL.txt")
    ]
    for label, beginning, ending in STAGES:
        directory = chain / label
        if not directory.is_dir():
            raise RuntimeError(f"missing restart-stage directory: {directory}")
        marker_path = directory / "RUN_OK_RESTART.txt"
        marker(marker_path, round(beginning / DT))
        sources.append((label, beginning, ending, directory, marker_path))

    chosen: dict[int, tuple[str, Path]] = {}
    inventory: list[dict[str, object]] = []
    marker_hashes: list[dict[str, str]] = []
    for label, beginning, ending, directory, marker_path in sources:
        fields = discover_raw_fields(directory)
        in_range = {
            step: path
            for step, path in fields.items()
            if round(beginning / DT) <= step <= round(ending / DT)
        }
        for step, path in in_range.items():
            # First occurrence is the preceding stage's final state at a
            # duplicated boundary and therefore the canonical restart input.
            chosen.setdefault(step, (label, path))
        inventory.append(
            {
                "stage": label,
                "directory": str(directory),
                "retained_fields_in_stage_range": len(in_range),
                "first_step": min(in_range) if in_range else None,
                "last_step": max(in_range) if in_range else None,
            }
        )
        marker_hashes.append(
            {"stage": label, "path": str(marker_path), "sha256": sha256(marker_path)}
        )

    required_tail = set(range(round(26.0 / DT), round(31.0 / DT) + 1, SAVE_STEPS))
    missing_tail = sorted(required_tail - set(chosen))
    if missing_tail:
        raise RuntimeError(
            "dense retained Re=1e6 t=26..31 sequence is incomplete; "
            f"first missing steps: {missing_tail[:8]}"
        )
    retained_mid = [
        step
        for step in chosen
        if round(21.0 / DT) <= step <= round(26.0 / DT)
    ]
    if len(retained_mid) < 10:
        raise RuntimeError(
            f"only {len(retained_mid)} retained Re=1e6 t=21..26 fields were found"
        )
    if round(6.0 / DT) not in chosen:
        raise RuntimeError("the canonical Re=1e6 t=6 checkpoint is missing")

    restart = output / "restart_data"
    restart.mkdir(parents=True, exist_ok=True)
    selected: list[dict[str, object]] = []
    for step, (stage, source) in sorted(chosen.items()):
        target = restart / f"lustre_{step}.dat"
        link(source, target)
        selected.append(
            {
                "step": step,
                "time": step * DT,
                "canonical_stage": stage,
                "source": str(source.resolve()),
                "bytes": source.stat().st_size,
            }
        )
    grids: dict[str, dict[str, object]] = {}
    for name in ("lustre_x_cb.dat", "lustre_y_cb.dat"):
        source = initial / "restart_data" / name
        if not source.is_file():
            raise RuntimeError(f"missing Re=1e6 grid coordinate file: {source}")
        link(source, restart / name)
        grids[name] = {
            "source": str(source.resolve()),
            "bytes": source.stat().st_size,
            "sha256": sha256(source),
        }

    report: dict[str, object] = {
        "status": "PASS",
        "purpose": "MACHINE_VISION_RAW_SOURCE_VIEW",
        "selection_rule": "PRECEDING_STAGE_FINAL_IS_CANONICAL_AT_DUPLICATED_BOUNDARY",
        "dt": DT,
        "initial": str(initial),
        "chain": str(chain),
        "retained_unique_fields": len(selected),
        "retained_t21_t26_fields": len(retained_mid),
        "dense_t26_t31_fields": len(required_tail),
        "stage_inventory": inventory,
        "completion_markers": marker_hashes,
        "grid_files": grids,
        "selected_fields": selected,
    }
    (output / "cv_raw_view_manifest.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "CV_RAW_VIEW_OK.txt").write_text(
        "status=PASS\n"
        f"retained_unique_fields={len(selected)}\n"
        f"retained_t21_t26_fields={len(retained_mid)}\n"
        f"dense_t26_t31_fields={len(required_tail)}\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial", type=Path, required=True)
    parser.add_argument("--chain", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.initial, args.chain, args.output)
    print(
        "MFC_CV_RAW_VIEW=PASS "
        f"fields={report['retained_unique_fields']} "
        f"dense_tail={report['dense_t26_t31_fields']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
