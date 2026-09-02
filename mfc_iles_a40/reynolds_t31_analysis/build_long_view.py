#!/usr/bin/env python3
"""Build a read-only, restart-continuous MFC field view from t=0 to t=31.

The long HLL calculation is intentionally split across restart-gated stage
directories.  MFC's visualisation reader expects one ``restart_data``
directory, so this utility creates a directory of symbolic links without
copying the large field files.  Duplicate boundary checkpoints are hashed and
must be byte-identical before the view is accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable


F270_FIELD_BYTES = 320_760_000
IB_STATE_BYTES = 160
DT = 1.0 / 5400.0
SAVE_STEPS = 270
STAGES = (
    ("t06_t11", 6.0, 11.0),
    ("t11_t16", 11.0, 16.0),
    ("t16_t21", 16.0, 21.0),
    ("t21_t26", 21.0, 26.0),
    ("t26_t31", 26.0, 31.0),
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def numeric_files(directory: Path, stem: str) -> dict[int, Path]:
    result: dict[int, Path] = {}
    pattern = re.compile(rf"{re.escape(stem)}_(\d+)\.dat$")
    for path in (directory / "restart_data").glob(f"{stem}_[0-9]*.dat"):
        match = pattern.fullmatch(path.name)
        if match:
            result[int(match.group(1))] = path.resolve()
    return result


def expected_steps(start_time: float, end_time: float) -> list[int]:
    start = round(start_time / DT)
    end = round(end_time / DT)
    if not (0.0 <= start_time < end_time <= 31.0):
        raise ValueError("the supported long-view interval is within 0 < t <= 31")
    if start % SAVE_STEPS or end % SAVE_STEPS:
        raise ValueError("start/end times must align with the saved 0.05-time grid")
    return list(range(start, end + 1, SAVE_STEPS))


def choose_sources(initial: Path, chain: Path) -> list[tuple[str, float, float, Path]]:
    sources: list[tuple[str, float, float, Path]] = [("t00_t06", 0.0, 6.0, initial)]
    for label, beginning, ending in STAGES:
        directory = chain / label
        if not directory.is_dir():
            raise RuntimeError(f"missing long-run stage directory: {directory}")
        sources.append((label, beginning, ending, directory))
    return sources


def check_size(path: Path, expected: int, description: str) -> None:
    actual = path.stat().st_size
    if actual != expected:
        raise RuntimeError(
            f"wrong {description} size for {path}: {actual}; expected {expected}"
        )


def link_file(source: Path, target: Path) -> None:
    if target.is_symlink() or target.exists():
        if target.resolve() == source.resolve():
            return
        target.unlink()
    target.symlink_to(source.resolve())


def boundary_audit(
    sources: Iterable[tuple[str, float, float, Path]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    sequence = list(sources)
    for left, right in zip(sequence[:-1], sequence[1:]):
        time = left[2]
        if time != right[1]:
            raise RuntimeError(f"non-contiguous stages: {left[0]} and {right[0]}")
        step = round(time / DT)
        record: dict[str, object] = {
            "time": time,
            "step": step,
            "left_stage": left[0],
            "right_stage": right[0],
        }
        for stem, expected in (("lustre", F270_FIELD_BYTES), ("ib_state", IB_STATE_BYTES)):
            first = left[3] / "restart_data" / f"{stem}_{step}.dat"
            second = right[3] / "restart_data" / f"{stem}_{step}.dat"
            if not first.is_file() or not second.is_file():
                raise RuntimeError(f"missing duplicated boundary {stem} at step {step}")
            check_size(first, expected, stem)
            check_size(second, expected, stem)
            first_sha = digest(first)
            second_sha = digest(second)
            record[f"{stem}_sha256"] = first_sha
            record[f"{stem}_identical"] = first_sha == second_sha
            if first_sha != second_sha:
                raise RuntimeError(
                    f"restart discontinuity: {stem}_{step}.dat differs across "
                    f"{left[0]} -> {right[0]}"
                )
        rows.append(record)
    return rows


def build(args: argparse.Namespace) -> dict[str, object]:
    initial = args.initial.resolve()
    chain = args.chain.resolve()
    output = args.output.resolve()
    if not initial.is_dir() or not chain.is_dir():
        raise RuntimeError("initial case or long-chain directory does not exist")
    if not (initial / "RUN_OK_INITIAL.txt").is_file():
        raise RuntimeError(f"missing initial-run PASS marker under {initial}")
    if not (chain / "t26_t31" / "RUN_OK_RESTART.txt").is_file():
        raise RuntimeError("the t=26..31 stage lacks RUN_OK_RESTART.txt")

    sources = choose_sources(initial, chain)
    boundaries = boundary_audit(sources)
    wanted = expected_steps(args.start_time, args.end_time)

    inventories: list[tuple[str, float, float, Path, dict[int, Path], dict[int, Path]]] = []
    for label, beginning, ending, directory in sources:
        inventories.append(
            (
                label,
                beginning,
                ending,
                directory,
                numeric_files(directory, "lustre"),
                numeric_files(directory, "ib_state"),
            )
        )

    restart = output / "restart_data"
    restart.mkdir(parents=True, exist_ok=True)
    links: list[dict[str, object]] = []
    for step in wanted:
        time = step * DT
        candidates = [
            entry for entry in inventories if entry[1] - 1e-12 <= time <= entry[2] + 1e-12
        ]
        selected = None
        for entry in reversed(candidates):
            if step in entry[4] and step in entry[5]:
                selected = entry
                break
        if selected is None:
            raise RuntimeError(f"missing synchronized fluid/IB snapshot at step {step}, t={time:g}")
        field = selected[4][step]
        ib_state = selected[5][step]
        check_size(field, F270_FIELD_BYTES, "f270 field")
        check_size(ib_state, IB_STATE_BYTES, "IB state")
        link_file(field, restart / field.name)
        link_file(ib_state, restart / ib_state.name)
        links.append(
            {
                "step": step,
                "time": time,
                "stage": selected[0],
                "field": str(field),
                "ib_state": str(ib_state),
            }
        )

    # Preserve every non-timestep Lustre companion needed by the pinned raw
    # restart reader (coordinate files and, when present, the static IB file).
    static_files: list[str] = []
    for source in sorted((initial / "restart_data").glob("lustre_*.dat")):
        if re.fullmatch(r"lustre_\d+\.dat", source.name):
            continue
        if source.stat().st_size == 0:
            raise RuntimeError(f"empty static restart companion: {source}")
        link_file(source, restart / source.name)
        static_files.append(source.name)
    for grid_name in ("lustre_x_cb.dat", "lustre_y_cb.dat"):
        if grid_name not in static_files:
            raise RuntimeError(f"missing grid metadata: {initial / 'restart_data' / grid_name}")
    for name in ("case.py", "Diamond_Airfoil_2D_MFC.stl"):
        source = initial / name
        if source.is_file():
            link_file(source, output / name)

    report: dict[str, object] = {
        "status": "PASS",
        "initial_case": str(initial),
        "chain": str(chain),
        "view": str(output),
        "time_range": [args.start_time, args.end_time],
        "dt": DT,
        "save_steps": SAVE_STEPS,
        "snapshots": len(links),
        "first_step": wanted[0],
        "last_step": wanted[-1],
        "boundary_identity": boundaries,
        "static_restart_companions": static_files,
        "files": links,
    }
    (output / "long_view_manifest.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "LONG_VIEW_OK.txt").write_text(
        "status=PASS\n"
        f"time_range={args.start_time:g}:{args.end_time:g}\n"
        f"snapshots={len(links)}\n"
        f"boundary_checks={len(boundaries)}\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial", type=Path, required=True)
    parser.add_argument("--chain", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-time", type=float, default=0.0)
    parser.add_argument("--end-time", type=float, default=31.0)
    args = parser.parse_args()
    report = build(args)
    print(
        f"LONG_VIEW=PASS snapshots={report['snapshots']} "
        f"boundaries={len(report['boundary_identity'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
