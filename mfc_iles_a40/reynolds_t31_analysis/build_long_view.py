#!/usr/bin/env python3
"""Build a read-only view of retained HLL restart fields through t=31.

These simulations wrote raw MPI-IO restart fields. Earlier dense fields were
post-processed into validated diagnostics/movies and deliberately pruned to
control storage, so a complete view must combine retained raw states with
those derived products. This program inventories that evidence before making
any symlinks and never modifies a solver directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Iterable

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
MOVIE_RANGE = re.compile(
    r"MFC_HLL_T(\d+)_T(\d+)_SCHLIEREN_VORTICITY\.mp4$"
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def expected_steps(start_time: float, end_time: float) -> list[int]:
    start = round(start_time / DT)
    end = round(end_time / DT)
    if not (0.0 <= start_time < end_time <= 31.0):
        raise ValueError("the supported long-view interval is 0 <= t <= 31")
    if start % SAVE_STEPS or end % SAVE_STEPS:
        raise ValueError("start/end times must align with the saved 0.05 grid")
    return list(range(start, end + 1, SAVE_STEPS))


def choose_sources(
    initial: Path, chain: Path
) -> list[tuple[str, float, float, Path]]:
    sources: list[tuple[str, float, float, Path]] = [
        ("t00_t06", 0.0, 6.0, initial)
    ]
    for label, beginning, ending in STAGES:
        directory = chain / label
        if not directory.is_dir():
            raise RuntimeError(f"missing long-run stage directory: {directory}")
        sources.append((label, beginning, ending, directory))
    return sources


def read_key_values(path: Path, description: str) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"missing {description}: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if "=" in raw_line:
            key, value = raw_line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def read_pass_marker(path: Path) -> dict[str, str]:
    values = read_key_values(path, "PASS marker")
    if values.get("status") != "PASS":
        raise RuntimeError(f"invalid PASS marker: {path}")
    return values


def stage_provenance(
    left: tuple[str, float, float, Path],
    right: tuple[str, float, float, Path],
    step: int,
) -> dict[str, object]:
    """Validate the immutable submission record for one restart handoff.

    MFC can rewrite the copied start-step output when a restarted simulation
    begins, so the two same-step files are not guaranteed to remain byte
    identical.  ``stage.env`` records the actual source/target relationship
    created by the chain submitter before either stage runs.
    """

    left_label, _left_start, boundary_time, left_dir = left
    right_label, right_start, right_stop, right_dir = right
    path = right_dir / "stage.env"
    values = read_key_values(path, "restart provenance record")
    expected_text = {
        "STAGE": right_label,
        "START_STEP": str(step),
        "STOP_STEP": str(round(right_stop / DT)),
    }
    for key, expected in expected_text.items():
        if values.get(key) != expected:
            raise RuntimeError(
                f"invalid {key} in {path}: {values.get(key)!r}; "
                f"expected {expected!r}"
            )
    expected_paths = {
        "SOURCE_DIR": left_dir.resolve(),
        "CASE_DIR": right_dir.resolve(),
    }
    for key, expected in expected_paths.items():
        raw_value = values.get(key)
        if raw_value is None or Path(raw_value).resolve() != expected:
            raise RuntimeError(
                f"invalid {key} in {path}: {raw_value!r}; expected {expected}"
            )
    for key, actual, expected in (
        ("START_TIME", values.get("START_TIME"), right_start),
        ("STOP_TIME", values.get("STOP_TIME"), right_stop),
    ):
        try:
            matches = math.isclose(float(actual), expected, abs_tol=1.0e-12)
        except (TypeError, ValueError):
            matches = False
        if not matches:
            raise RuntimeError(
                f"invalid {key} in {path}: {actual!r}; expected {expected:g}"
            )
    if not math.isclose(boundary_time, right_start, abs_tol=1.0e-12):
        raise RuntimeError(
            f"internal stage boundary mismatch: {left_label} -> {right_label}"
        )
    return {
        "path": str(path.resolve()),
        "sha256": digest(path),
        "source_directory": str(left_dir.resolve()),
        "target_directory": str(right_dir.resolve()),
        "valid": True,
    }


def csv_coverage(path: Path, required: Iterable[str]) -> dict[str, object]:
    required = tuple(required)
    value_columns = tuple(name for name in required if name != "time")
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        names = set(reader.fieldnames or [])
        missing = sorted(set(required) - names)
        if missing:
            raise RuntimeError(f"{path} lacks columns {missing}")
        times: list[float] = []
        finite_values = {name: 0 for name in value_columns}
        for row in reader:
            try:
                time = float(row["time"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"invalid time value in {path}") from exc
            if not math.isfinite(time):
                raise RuntimeError(f"non-finite time value in {path}")
            times.append(time)
            for name in value_columns:
                try:
                    value = float(row[name])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    finite_values[name] += 1
    if not times:
        raise RuntimeError(f"empty diagnostic table: {path}")
    if len(set(times)) != len(times):
        raise RuntimeError(f"duplicated diagnostic times in {path}")
    times = sorted(times)
    spacings = [b - a for a, b in zip(times, times[1:])]
    if spacings and min(spacings) <= 0.0:
        raise RuntimeError(f"non-increasing diagnostic times in {path}")
    return {
        "path": str(path.resolve()),
        "rows": len(times),
        "time_start": times[0],
        "time_end": times[-1],
        "minimum_spacing": float(min(spacings)) if spacings else None,
        "maximum_spacing": float(max(spacings)) if spacings else None,
        "finite_values": finite_values,
    }


def diagnostic_sources(chain: Path) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for force in chain.rglob("*force_history.csv"):
        if not force.is_file():
            continue
        shock = force.with_name(
            force.name.replace("force_history", "shock_history")
        )
        if not shock.is_file():
            continue
        try:
            force_info = csv_coverage(force, ("time", "CL", "CD"))
            shock_info = csv_coverage(
                shock, ("time", "stand_off_over_c")
            )
        except RuntimeError:
            continue
        if (
            float(force_info["time_end"]) < 20.95
            or float(force_info["time_start"]) > 6.05
            or float(shock_info["time_end"]) < 20.95
            or float(shock_info["time_start"]) > 6.05
            or int(force_info["rows"]) < 290
            or int(shock_info["rows"]) < 290
            or float(force_info["maximum_spacing"] or math.inf) > 0.051
            or float(shock_info["maximum_spacing"] or math.inf) > 0.051
            or any(
                int(force_info["finite_values"][name])
                != int(force_info["rows"])
                for name in ("CL", "CD")
            )
            or int(shock_info["finite_values"]["stand_off_over_c"])
            < max(16, math.ceil(0.5 * int(shock_info["rows"])))
        ):
            continue
        candidates.append(
            {
                "force": force_info,
                "shock": shock_info,
                "mtime": force.stat().st_mtime,
            }
        )
    candidates.sort(
        key=lambda row: (
            float(row["force"]["time_end"]),
            int(row["force"]["rows"]),
            float(row["mtime"]),
        ),
        reverse=True,
    )
    return candidates


def movie_sources(initial: Path, chain: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[Path] = set()
    for root in (chain, initial):
        for path in root.rglob(
            "MFC_HLL_T*_T*_SCHLIEREN_VORTICITY.mp4"
        ):
            if not path.is_file() or path.stat().st_size < 1_000_000:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            match = MOVIE_RANGE.fullmatch(path.name)
            if match is None:
                continue
            beginning, end = (
                float(match.group(1)),
                float(match.group(2)),
            )
            if end <= beginning:
                continue
            seen.add(resolved)
            rows.append(
                {
                    "path": str(resolved),
                    "time_start": beginning,
                    "time_end": end,
                    "bytes": path.stat().st_size,
                    "mtime": path.stat().st_mtime,
                }
            )
    rows.sort(
        key=lambda row: (
            float(row["time_start"]),
            -float(row["time_end"]),
        )
    )
    return rows


def choose_movie_prefix(
    rows: list[dict[str, object]], target: float = 26.0
) -> list[dict[str, object]]:
    chosen: list[dict[str, object]] = []
    current = 0.0
    while current < target - 1.0e-9:
        eligible = [
            row
            for row in rows
            if float(row["time_start"]) <= current + 1.0e-9
            and float(row["time_end"]) > current + 1.0e-9
        ]
        if not eligible:
            break
        best = max(
            eligible,
            key=lambda row: (
                float(row["time_end"]),
                float(row["mtime"]),
            ),
        )
        chosen.append(best)
        current = float(best["time_end"])
    return chosen if current >= target - 1.0e-9 else []


def inventory(initial: Path, chain: Path) -> dict[str, object]:
    if not (initial / "RUN_OK_INITIAL.txt").is_file():
        raise RuntimeError(
            f"missing initial-run PASS marker under {initial}"
        )
    sources = choose_sources(initial, chain)
    raw: list[dict[str, object]] = []
    fields_by_stage: dict[str, dict[int, Path]] = {}
    all_fields: dict[int, tuple[str, Path]] = {}
    for label, beginning, ending, directory in sources:
        fields = discover_raw_fields(directory)
        fields_by_stage[label] = fields
        for step, path in fields.items():
            if (
                round(beginning / DT)
                <= step
                <= round(ending / DT)
            ):
                # The preceding stage's final checkpoint is the canonical
                # restart source.  A later stage may rewrite its copied
                # start-step file when MFC begins the restarted simulation.
                all_fields.setdefault(step, (label, path))
        raw.append(
            {
                "stage": label,
                "directory": str(directory.resolve()),
                "time_range": [beginning, ending],
                "raw_fields": len(fields),
                "first_step": min(fields) if fields else None,
                "last_step": max(fields) if fields else None,
            }
        )

    diagnostics = diagnostic_sources(chain)
    if not diagnostics:
        raise RuntimeError(
            "no validated dense force/shock history covering t=6..21 "
            "was found; the pruned raw fields cannot be recreated"
        )
    selected_diagnostics = diagnostics[0]
    for kind in ("force", "shock"):
        selected_diagnostics[kind]["sha256"] = digest(
            Path(str(selected_diagnostics[kind]["path"]))
        )
    dense_end = float(selected_diagnostics["force"]["time_end"])

    dense_t26 = set(
        range(
            round(26.0 / DT),
            round(31.0 / DT) + 1,
            SAVE_STEPS,
        )
    )
    available = set(all_fields)
    missing_t26 = sorted(dense_t26 - available)
    if missing_t26:
        raise RuntimeError(
            f"t=26..31 raw history is incomplete "
            f"({len(missing_t26)} missing); first: {missing_t26[:8]}"
        )
    sparse_t21 = sorted(
        step
        for step in available
        if round(21.0 / DT)
        <= step
        <= round(26.0 / DT)
    )
    if len(sparse_t21) < 10:
        raise RuntimeError(
            f"only {len(sparse_t21)} retained t=21..26 checkpoints "
            "were found; expected at least 10"
        )

    movies = movie_sources(initial, chain)
    chosen_movies = choose_movie_prefix(movies, 26.0)
    if not chosen_movies:
        raise RuntimeError(
            "no validated HLL movie sequence covering t=0..26 was found"
        )
    for row in chosen_movies:
        row["sha256"] = digest(Path(str(row["path"])))

    boundaries: list[dict[str, object]] = []
    for left, right in zip(sources[:-1], sources[1:]):
        step = round(left[2] / DT)
        provenance = stage_provenance(left, right, step)
        marker_path = right[3] / "RUN_OK_RESTART.txt"
        marker = read_pass_marker(marker_path)
        if int(marker.get("start_step", "-1")) != step:
            raise RuntimeError(f"wrong start_step in {marker_path}")
        first = fields_by_stage[left[0]].get(step)
        second = fields_by_stage[right[0]].get(step)
        record: dict[str, object] = {
            "time": left[2],
            "step": step,
            "left_stage": left[0],
            "right_stage": right[0],
            "right_restart_marker": str(marker_path.resolve()),
            "right_restart_marker_sha256": digest(marker_path),
            "right_restart_marker_valid": True,
            "stage_provenance": provenance,
            "left_raw_present": first is not None,
            "right_raw_present": second is not None,
        }
        if first is not None and second is not None:
            first_sha, second_sha = digest(first), digest(second)
            record.update(
                audit_status=(
                    "BYTE_IDENTICAL_RAW"
                    if first_sha == second_sha
                    else "NONIDENTICAL_RAW_PLUS_CHAIN_PROVENANCE"
                ),
                raw_byte_identity=first_sha == second_sha,
                left_sha256=first_sha,
                right_sha256=second_sha,
            )
        elif first is not None or second is not None:
            record["audit_status"] = (
                "SINGLE_RETAINED_RAW_PLUS_MARKER"
            )
        elif 6.0 - 1.0e-9 <= left[2] <= dense_end + 1.0e-9:
            record["audit_status"] = "DERIVED_HISTORY_PLUS_MARKER"
        else:
            raise RuntimeError(
                f"boundary t={left[2]:g} has neither a retained raw "
                "state nor derived-history coverage"
            )
        boundaries.append(record)

    return {
        "status": "PASS",
        "initial_case": str(initial.resolve()),
        "chain": str(chain.resolve()),
        "dt": DT,
        "save_steps": SAVE_STEPS,
        "raw_stage_inventory": raw,
        "retained_raw_fields": len(all_fields),
        "retained_raw_first_step": min(all_fields),
        "retained_raw_last_step": max(all_fields),
        "t21_t26_retained_samples": len(sparse_t21),
        "t26_t31_dense_samples": len(dense_t26),
        "selected_diagnostics": selected_diagnostics,
        "selected_movie_prefix": chosen_movies,
        "boundary_identity": boundaries,
        "all_fields": all_fields,
    }


def link_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        if target.resolve() == source.resolve():
            return
        target.unlink()
    target.symlink_to(source.resolve())


def build(args: argparse.Namespace) -> dict[str, object]:
    initial = args.initial.resolve()
    chain = args.chain.resolve()
    report = inventory(initial, chain)
    if args.check_only:
        report.pop("all_fields")
        return report

    output = args.output.resolve()
    restart = output / "restart_data"
    restart.mkdir(parents=True, exist_ok=True)
    links: list[dict[str, object]] = []
    all_fields: dict[int, tuple[str, Path]] = report.pop("all_fields")
    for step, (stage, field) in sorted(all_fields.items()):
        link_file(field, restart / field.name)
        links.append(
            {
                "step": step,
                "time": step * DT,
                "stage": stage,
                "field": str(field),
            }
        )

    # Native IB records are tiny. Retain every available record; the analyzer
    # uses them only if the sequence is complete and finite.
    ib_links = 0
    for _label, _beginning, _ending, directory in choose_sources(
        initial, chain
    ):
        for source in sorted(
            (directory / "restart_data").glob(
                "ib_state_[0-9]*.dat"
            )
        ):
            match = re.fullmatch(
                r"ib_state_(\d+)\.dat", source.name
            )
            if (
                match is None
                or not source.is_file()
                or source.stat().st_size != 160
            ):
                continue
            target = restart / source.name
            if not target.exists() and not target.is_symlink():
                link_file(source, target)
                ib_links += 1

    for name in ("lustre_x_cb.dat", "lustre_y_cb.dat"):
        source = initial / "restart_data" / name
        if not source.is_file() or source.stat().st_size == 0:
            raise RuntimeError(f"missing raw-grid companion: {source}")
        link_file(source, restart / name)
    for name in ("case.py", "Diamond_Airfoil_2D_MFC.stl"):
        source = initial / name
        if source.is_file():
            link_file(source, output / name)

    report.update(
        view=str(output),
        time_range=[args.start_time, args.end_time],
        snapshots=len(links),
        first_step=links[0]["step"],
        last_step=links[-1]["step"],
        native_ib_records=ib_links,
        files=links,
        boundary_audit_status=(
            "PASS_HYBRID_RETAINED_AND_DERIVED"
        ),
    )
    (output / "long_view_manifest.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "LONG_VIEW_OK.txt").write_text(
        "status=PASS\n"
        "input_model=HYBRID_RETAINED_RAW_PLUS_VALIDATED_DERIVED\n"
        f"retained_raw_snapshots={len(links)}\n"
        f"t21_t26_retained_samples="
        f"{report['t21_t26_retained_samples']}\n"
        f"t26_t31_dense_samples={report['t26_t31_dense_samples']}\n"
        f"boundary_checks={len(report['boundary_identity'])}\n",
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
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    expected_steps(args.start_time, args.end_time)
    report = build(args)
    print(
        f"LONG_VIEW_PREFLIGHT=PASS "
        f"retained_raw={report['retained_raw_fields']} "
        f"t26_t31={report['t26_t31_dense_samples']} "
        f"derived_to="
        f"{report['selected_diagnostics']['force']['time_end']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
