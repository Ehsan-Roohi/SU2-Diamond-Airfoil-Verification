#!/usr/bin/env python3
"""Restartable Unity runner for the validated alpha=40 medium-half-dt stage.

This runner is intentionally fail-closed.  It consumes the archived steady
iteration-20000 seed, preserves its scientific configuration, repeats the
validated BDF1 bootstrap, and then advances only the ``medium_halfdt`` BDF2
stage.  It never advances to another grid/time-step matrix member and never
claims qualification.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


EXPECTED_SEED_NAME = "URANS_alpha40_seed_checkpoint_iter20000.zip"
CASE_NAME = "medium_halfdt"
MESH_BASENAME = "diamond_medium_720x181.su2"
EXPECTED_MESH_POINTS = 130_320
PHYSICAL_DT = 2.5e-6
BOOTSTRAP_INNER_ITER = 2_000
PRODUCTION_INNER_ITER = 600
DEFAULT_TARGET_STEP = 12_000
DEFAULT_CHUNK_STEPS = 20
RESTART_STEM = "restart_medium_halfdt"
RESUME_ARCHIVE_NAME = (
    "URANS_alpha40_medium_halfdt_checkpoint_t000664.zip"
)
RESUME_PARTS_DIRNAME = RESUME_ARCHIVE_NAME + ".parts"
RESUME_CHECKPOINT_RE = re.compile(
    rf"^URANS_alpha40_{CASE_NAME}_checkpoint_t(\d{{6}})\.zip$"
)

REQUIRED_SCIENCE = {
    "SOLVER": "RANS",
    "KIND_TURB_MODEL": "SST",
    "MACH_NUMBER": 3.0,
    "AOA": 40.0,
    "REYNOLDS_NUMBER": 1.0e6,
}

OPERATIONAL_KEYS = {
    "CONV_FILENAME",
    "DISCARD_INFILES",
    "HISTORY_OUTPUT",
    "INNER_ITER",
    "ITER",
    "MESH_FILENAME",
    "OUTPUT_FILES",
    "OUTPUT_WRT_FREQ",
    "READ_BINARY_RESTART",
    "RESTART_FILENAME",
    "RESTART_ITER",
    "RESTART_SOL",
    "SCREEN_OUTPUT",
    "SCREEN_WRT_FREQ_INNER",
    "SOLUTION_FILENAME",
    "SURFACE_FILENAME",
    "TABULAR_FORMAT",
    "TIME_DOMAIN",
    "TIME_ITER",
    "TIME_MARCHING",
    "TIME_STEP",
    "VOLUME_FILENAME",
}

NONPHYSICAL_RE = re.compile(r"\bnon[- ]?physical\b", re.IGNORECASE)
COUNT_BEFORE_NONPHYSICAL_RE = re.compile(
    r"(\d+)\s+non[- ]?physical", re.IGNORECASE
)
RESTART_RE = re.compile(rf"^{re.escape(RESTART_STEM)}_(\d+)\.(csv|dat)$")

STOP_REQUESTED = False
ACTIVE_SOLVER: subprocess.Popen[str] | None = None


class GateFailure(RuntimeError):
    """A fail-closed scientific or restart gate failed."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_cfg_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("%", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().upper()] = value.strip()
    return values


def numeric(value: str) -> float:
    return float(value.strip().rstrip(","))


def validate_science(values: dict[str, str], source: str) -> None:
    for key, expected in REQUIRED_SCIENCE.items():
        if key not in values:
            raise GateFailure(f"{source}: missing required key {key}")
        if isinstance(expected, str):
            if values[key].upper() != expected:
                raise GateFailure(
                    f"{source}: {key}={values[key]} (required {expected})"
                )
        else:
            try:
                actual = numeric(values[key])
            except ValueError as exc:
                raise GateFailure(f"{source}: nonnumeric {key}={values[key]}") from exc
            if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
                raise GateFailure(
                    f"{source}: {key}={actual:g} (required {expected:g})"
                )
    mesh_name = Path(values.get("MESH_FILENAME", "")).name
    if mesh_name != MESH_BASENAME:
        raise GateFailure(
            f"{source}: MESH_FILENAME resolves to {mesh_name!r}; "
            f"required {MESH_BASENAME!r}"
        )


def scientific_fingerprint(values: dict[str, str]) -> str:
    retained = {
        key: value
        for key, value in values.items()
        if key not in OPERATIONAL_KEYS
    }
    payload = json.dumps(retained, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def safe_archive_member(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def inspect_seed(seed_zip: Path) -> dict[str, Any]:
    if not seed_zip.is_file():
        raise GateFailure(f"missing seed checkpoint: {seed_zip}")
    if seed_zip.name != EXPECTED_SEED_NAME:
        raise GateFailure(
            f"seed must retain the audited filename {EXPECTED_SEED_NAME}"
        )

    with zipfile.ZipFile(seed_zip) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        unsafe = [item.filename for item in members if not safe_archive_member(item.filename)]
        if unsafe:
            raise GateFailure(f"unsafe paths in seed archive: {unsafe[:3]}")

        configs: list[tuple[str, str, dict[str, str]]] = []
        for item in members:
            if item.filename.lower().endswith(".cfg"):
                text = archive.read(item).decode("utf-8", errors="strict")
                values = parse_cfg_text(text)
                try:
                    validate_science(values, item.filename)
                except GateFailure:
                    continue
                configs.append((item.filename, text, values))
        if not configs:
            raise GateFailure(
                "seed archive has no cfg matching RANS/SST, Mach=3, alpha=40, "
                "Re=1e6, and the medium mesh"
            )
        fingerprints = {scientific_fingerprint(values) for _, _, values in configs}
        if len(fingerprints) != 1:
            names = [name for name, _, _ in configs]
            raise GateFailure(f"ambiguous scientific cfg files in seed: {names}")
        configs.sort(
            key=lambda row: (
                row[2].get("RESTART_SOL", "NO").upper() == "YES",
                numeric(row[2].get("ITER", "0")),
                row[0],
            ),
            reverse=True,
        )
        cfg_name, cfg_text, cfg_values = configs[0]

        restart_candidates = [
            item
            for item in members
            if Path(item.filename).suffix.lower() in {".csv", ".dat"}
            and any(
                token in Path(item.filename).name.lower()
                for token in ("restart", "solution")
            )
            and "history" not in Path(item.filename).name.lower()
            and "surface" not in Path(item.filename).name.lower()
            and item.file_size > 0
        ]
        if not restart_candidates:
            raise GateFailure("seed archive has no nonempty CSV/DAT restart")
        restart_candidates.sort(
            key=lambda item: (
                "20000" in Path(item.filename).stem,
                item.file_size,
                item.filename,
            ),
            reverse=True,
        )
        restart = restart_candidates[0]
        restart_bytes = archive.read(restart)
        restart_extension = Path(restart.filename).suffix.lower()
        if restart_extension == ".csv":
            header = restart_bytes.splitlines()[0].decode("utf-8", errors="replace")
            if "Point" not in header and "point" not in header:
                raise GateFailure(
                    f"selected restart {restart.filename} has no PointID-like header"
                )

    return {
        "seed_path": str(seed_zip.resolve()),
        "seed_sha256": sha256_file(seed_zip),
        "seed_size_bytes": seed_zip.stat().st_size,
        "config_member": cfg_name,
        "config_sha256": sha256_bytes(cfg_text.encode("utf-8")),
        "scientific_fingerprint": scientific_fingerprint(cfg_values),
        "restart_member": restart.filename,
        "restart_extension": restart_extension,
        "restart_sha256": sha256_bytes(restart_bytes),
        "restart_size_bytes": len(restart_bytes),
    }


def json_archive_member(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(archive.read(name).decode("utf-8", errors="strict"))
    except KeyError as exc:
        raise GateFailure(f"resume checkpoint is missing {name}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateFailure(f"resume checkpoint has invalid JSON in {name}") from exc
    if not isinstance(payload, dict):
        raise GateFailure(f"resume checkpoint {name} must contain a JSON object")
    return payload


def inspect_resume_checkpoint(
    checkpoint_zip: Path, seed_zip: Path
) -> dict[str, Any]:
    if not checkpoint_zip.is_file():
        raise GateFailure(f"missing resume checkpoint: {checkpoint_zip}")
    name_match = RESUME_CHECKPOINT_RE.match(checkpoint_zip.name)
    if not name_match:
        raise GateFailure(
            "resume checkpoint filename does not match the audited alpha40 case"
        )
    filename_step = int(name_match.group(1))
    seed_info = inspect_seed(seed_zip)

    with zipfile.ZipFile(checkpoint_zip) as archive:
        members = {
            item.filename: item
            for item in archive.infolist()
            if not item.is_dir()
        }
        unsafe = [name for name in members if not safe_archive_member(name)]
        if unsafe:
            raise GateFailure(f"unsafe paths in resume checkpoint: {unsafe[:3]}")
        manifest = json_archive_member(archive, "checkpoint_manifest.json")
        status = json_archive_member(archive, "status.json")
        seed_manifest = json_archive_member(archive, "seed/seed_manifest.json")

        step = manifest.get("time_step")
        if not isinstance(step, int) or step != filename_step:
            raise GateFailure("resume checkpoint step disagrees with its filename")
        if step < 1 or step >= DEFAULT_TARGET_STEP:
            raise GateFailure("resume checkpoint step is outside the production range")
        if manifest.get("checkpoint_file") != checkpoint_zip.name:
            raise GateFailure("resume manifest has the wrong checkpoint filename")
        for source, payload in (("manifest", manifest), ("status", status)):
            if payload.get("case") != CASE_NAME:
                raise GateFailure(f"resume {source} has the wrong case")
            if payload.get("status") != "CHECKPOINTED":
                raise GateFailure(f"resume {source} is not CHECKPOINTED")
            if payload.get("qualification") != "NOT_QUALIFIED":
                raise GateFailure(f"resume {source} has unexpected qualification")
            if payload.get("time_step") != step:
                raise GateFailure(f"resume {source} has an inconsistent time step")
            if payload.get("nonphysical_points") != 0:
                raise GateFailure(f"resume {source} reports nonphysical points")
            if payload.get("target_time_step") != DEFAULT_TARGET_STEP:
                raise GateFailure(f"resume {source} has the wrong target step")
            try:
                resume_dt = float(payload.get("dt_seconds"))
            except (TypeError, ValueError) as exc:
                raise GateFailure(f"resume {source} has an invalid time step size") from exc
            if not math.isclose(resume_dt, PHYSICAL_DT, rel_tol=0.0, abs_tol=1e-15):
                raise GateFailure(f"resume {source} has the wrong time step size")

        if seed_manifest.get("seed_sha256") != seed_info["seed_sha256"]:
            raise GateFailure("resume checkpoint was not derived from the bundled seed")
        restart_extension = seed_manifest.get("restart_extension")
        if restart_extension not in {".csv", ".dat"}:
            raise GateFailure("resume checkpoint has no supported restart extension")
        if "seed/seed_original.cfg" not in members:
            raise GateFailure("resume checkpoint is missing seed/seed_original.cfg")
        base_cfg = archive.read("seed/seed_original.cfg").decode(
            "utf-8", errors="strict"
        )
        validate_science(parse_cfg_text(base_cfg), "resume seed config")

        included = manifest.get("included_sha256")
        if not isinstance(included, dict) or not included:
            raise GateFailure("resume checkpoint has no included_sha256 manifest")
        for member_name, expected_sha in included.items():
            if not isinstance(member_name, str) or not safe_archive_member(member_name):
                raise GateFailure("resume checkpoint manifest contains an unsafe path")
            if member_name not in members:
                raise GateFailure(f"resume checkpoint is missing {member_name}")
            if not isinstance(expected_sha, str):
                raise GateFailure(f"resume checksum is invalid for {member_name}")
            actual_sha = sha256_bytes(archive.read(member_name))
            if actual_sha != expected_sha:
                raise GateFailure(f"resume checksum mismatch for {member_name}")

        required = [
            "status.json",
            "seed/seed_manifest.json",
            "seed/seed_original.cfg",
            *[
                f"{RESTART_STEM}_{level:05d}{restart_extension}"
                for level in (step - 1, step)
            ],
        ]
        missing = [name for name in required if name not in included]
        if missing:
            raise GateFailure(f"resume checkpoint lacks BDF2 restart levels: {missing}")

    return {
        "checkpoint_path": str(checkpoint_zip.resolve()),
        "checkpoint_sha256": sha256_file(checkpoint_zip),
        "checkpoint_size_bytes": checkpoint_zip.stat().st_size,
        "time_step": step,
        "restart_extension": restart_extension,
        "included_sha256": included,
        "manifest": manifest,
    }


def restore_checkpoint(
    checkpoint_zip: Path,
    seed_zip: Path,
    repo_root: Path,
    run_root: Path,
) -> dict[str, Any]:
    info = inspect_resume_checkpoint(checkpoint_zip, seed_zip)
    validate_mesh(repo_root)
    if latest_restart_step(run_root) is not None or (run_root / "status.json").exists():
        raise GateFailure("refusing to overwrite an initialized run directory")
    run_root.mkdir(parents=True, exist_ok=True)
    included = info["included_sha256"]
    with zipfile.ZipFile(checkpoint_zip) as archive:
        for member_name in included:
            destination = run_root / member_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(archive.read(member_name))
            os.replace(temporary, destination)
    write_json(run_root / "seed" / "resume_checkpoint_manifest.json", info["manifest"])
    if latest_restart_step(run_root) != info["time_step"]:
        raise GateFailure("restored checkpoint did not reproduce its latest time step")
    require_restart_levels(
        run_root, info["time_step"] + 1, info["restart_extension"]
    )
    return info


def assemble_resume_parts(parts_dir: Path, output_path: Path) -> dict[str, Any]:
    manifest_path = parts_dir / "manifest.json"
    if not manifest_path.is_file():
        raise GateFailure(f"missing bundled resume manifest: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateFailure("bundled resume manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise GateFailure("bundled resume manifest must be a JSON object")
    if manifest.get("archive_name") != RESUME_ARCHIVE_NAME:
        raise GateFailure("bundled resume manifest has the wrong archive name")
    if output_path.name != RESUME_ARCHIVE_NAME:
        raise GateFailure(f"resume output must be named {RESUME_ARCHIVE_NAME}")
    expected_size = manifest.get("archive_size_bytes")
    expected_sha = manifest.get("archive_sha256")
    parts = manifest.get("parts")
    if not isinstance(expected_size, int) or expected_size < 1:
        raise GateFailure("bundled resume manifest has an invalid archive size")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise GateFailure("bundled resume manifest has an invalid archive checksum")
    if not isinstance(parts, list) or not parts:
        raise GateFailure("bundled resume manifest has no parts")

    if output_path.is_file():
        if (
            output_path.stat().st_size == expected_size
            and sha256_file(output_path) == expected_sha
        ):
            return {
                "archive_path": str(output_path.resolve()),
                "archive_sha256": expected_sha,
                "archive_size_bytes": expected_size,
                "part_count": len(parts),
                "reused": True,
            }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    archive_digest = hashlib.sha256()
    written = 0
    with temporary.open("wb") as destination:
        for index, entry in enumerate(parts):
            if not isinstance(entry, dict):
                raise GateFailure(f"bundled resume part {index} is invalid")
            name = entry.get("name")
            size = entry.get("size_bytes")
            expected_part_sha = entry.get("sha256")
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or not safe_archive_member(name)
            ):
                raise GateFailure(f"bundled resume part {index} has an unsafe name")
            if not isinstance(size, int) or size < 1:
                raise GateFailure(f"bundled resume part {name} has an invalid size")
            if not isinstance(expected_part_sha, str) or len(expected_part_sha) != 64:
                raise GateFailure(f"bundled resume part {name} has an invalid checksum")
            part_path = parts_dir / name
            if not part_path.is_file() or part_path.stat().st_size != size:
                raise GateFailure(f"bundled resume part is missing or truncated: {name}")
            data = part_path.read_bytes()
            if sha256_bytes(data) != expected_part_sha:
                raise GateFailure(f"bundled resume checksum mismatch for {name}")
            destination.write(data)
            archive_digest.update(data)
            written += len(data)
    if written != expected_size or archive_digest.hexdigest() != expected_sha:
        temporary.unlink(missing_ok=True)
        raise GateFailure("reassembled resume archive failed its final checksum")
    os.replace(temporary, output_path)
    return {
        "archive_path": str(output_path.resolve()),
        "archive_sha256": expected_sha,
        "archive_size_bytes": expected_size,
        "part_count": len(parts),
        "reused": False,
    }


def mesh_point_count(mesh: Path) -> int:
    with mesh.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = re.match(r"\s*NPOIN\s*=\s*(\d+)", line)
            if match:
                return int(match.group(1))
    raise GateFailure(f"NPOIN was not found in mesh {mesh}")


def validate_mesh(repo_root: Path) -> dict[str, Any]:
    mesh = repo_root / "meshes" / MESH_BASENAME
    if not mesh.is_file():
        raise GateFailure(f"missing audited medium mesh: {mesh}")
    count = mesh_point_count(mesh)
    if count != EXPECTED_MESH_POINTS:
        raise GateFailure(
            f"mesh {mesh.name} has NPOIN={count}; required {EXPECTED_MESH_POINTS}"
        )
    return {
        "mesh_path": str(mesh.resolve()),
        "mesh_sha256": sha256_file(mesh),
        "mesh_points": count,
    }


def cfg_with_overrides(
    base_text: str,
    overrides: dict[str, str],
    remove_keys: set[str] | None = None,
) -> str:
    pending = {key.upper(): value for key, value in overrides.items()}
    removed = {key.upper() for key in (remove_keys or set())}
    output: list[str] = []
    seen: set[str] = set()
    for raw in base_text.splitlines():
        code = raw.split("%", 1)[0]
        if "=" not in code:
            output.append(raw)
            continue
        key = code.split("=", 1)[0].strip().upper()
        if key in removed:
            continue
        if key in pending:
            if key in seen:
                continue
            output.append(f"{key}= {pending[key]}")
            seen.add(key)
        else:
            output.append(raw)
    missing = [key for key in pending if key not in seen]
    if missing:
        output.extend(["", "% Unity restart-control overrides"])
        output.extend(f"{key}= {pending[key]}" for key in missing)
    return "\n".join(output).rstrip() + "\n"


def prepare_seed(seed_zip: Path, repo_root: Path, run_root: Path) -> dict[str, Any]:
    seed_info = inspect_seed(seed_zip)
    mesh_info = validate_mesh(repo_root)
    run_root.mkdir(parents=True, exist_ok=True)
    seed_dir = run_root / "seed"
    seed_dir.mkdir(exist_ok=True)

    with zipfile.ZipFile(seed_zip) as archive:
        cfg_bytes = archive.read(seed_info["config_member"])
        restart_bytes = archive.read(seed_info["restart_member"])

    cfg_path = seed_dir / "seed_original.cfg"
    cfg_path.write_bytes(cfg_bytes)
    restart_path = run_root / f"{RESTART_STEM}_00000{seed_info['restart_extension']}"
    restart_path.write_bytes(restart_bytes)
    if sha256_file(restart_path) != seed_info["restart_sha256"]:
        raise GateFailure("seed restart failed its post-copy SHA-256 check")

    manifest = {
        "created_utc": utc_now(),
        "case": CASE_NAME,
        "scientific_status": "PREPARING",
        "qualification": "NOT_QUALIFIED",
        "dt_seconds": PHYSICAL_DT,
        "bootstrap_inner_iter": BOOTSTRAP_INNER_ITER,
        "production_inner_iter": PRODUCTION_INNER_ITER,
        **seed_info,
        **mesh_info,
    }
    write_json(seed_dir / "seed_manifest.json", manifest)
    return manifest


def write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def latest_restart_step(run_root: Path) -> int | None:
    steps: list[int] = []
    for path in run_root.glob(f"{RESTART_STEM}_*"):
        match = RESTART_RE.match(path.name)
        if match and path.stat().st_size > 0:
            steps.append(int(match.group(1)))
    return max(steps) if steps else None


def restart_path(run_root: Path, step: int, extension: str = ".csv") -> Path:
    if extension not in {".csv", ".dat"}:
        raise GateFailure(f"unsupported restart extension: {extension}")
    return run_root / f"{RESTART_STEM}_{step:05d}{extension}"


def require_restart_levels(
    run_root: Path, next_step: int, extension: str = ".csv"
) -> None:
    required = [next_step - 1] if next_step == 1 else [next_step - 2, next_step - 1]
    missing = [
        str(restart_path(run_root, step, extension))
        for step in required
        if not restart_path(run_root, step, extension).is_file()
    ]
    if missing:
        raise GateFailure(f"missing restart level(s) for step {next_step}: {missing}")


def normalized(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def find_column(fields: list[str], candidates: tuple[str, ...]) -> str | None:
    mapping = {normalized(field): field for field in fields}
    for candidate in candidates:
        if normalized(candidate) in mapping:
            return mapping[normalized(candidate)]
    for clean, original in mapping.items():
        if any(normalized(candidate) in clean for candidate in candidates):
            return original
    return None


def last_metrics(history: Path) -> dict[str, float | int | None]:
    if not history.is_file():
        raise GateFailure(f"solver did not create history file {history}")
    with history.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise GateFailure(f"history file {history} has no rows")
    fields = list(rows[0])
    row = rows[-1]
    candidates = {
        "time_iter": ("TIME_ITER",),
        "inner_iter": ("INNER_ITER",),
        "cl": ("LIFT", "CL"),
        "cd": ("DRAG", "CD"),
        "rms_density": ("RMS_DENSITY", "RMS[RHO]", "RMSRHO"),
        "rms_tke": ("RMS_TKE", "RMS[K]", "RMSTKE"),
        "rms_omega": ("RMS_DISSIPATION", "RMS[OMEGA]", "RMSOMEGA"),
    }
    result: dict[str, float | int | None] = {}
    for label, names in candidates.items():
        column = find_column(fields, names)
        if column is None:
            result[label] = None
            continue
        try:
            value = float(row[column])
        except (KeyError, TypeError, ValueError) as exc:
            raise GateFailure(f"unreadable {label} in {history}") from exc
        if not math.isfinite(value):
            raise GateFailure(f"nonfinite {label}={value} in {history}")
        result[label] = int(value) if label.endswith("iter") else value
    return result


def nonphysical_count(paths: list[Path]) -> tuple[int, list[str]]:
    maximum = 0
    matches: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not NONPHYSICAL_RE.search(line):
                continue
            matches.append(line.strip())
            found = COUNT_BEFORE_NONPHYSICAL_RE.search(line)
            maximum = max(maximum, int(found.group(1)) if found else 1)
    return maximum, matches


def make_config(
    base_text: str,
    repo_root: Path,
    start_step: int,
    end_step: int,
    restart_extension: str = ".csv",
) -> tuple[str, str]:
    bootstrap = start_step == 1
    mode = (
        "DUAL_TIME_STEPPING-1ST_ORDER"
        if bootstrap
        else "DUAL_TIME_STEPPING-2ND_ORDER"
    )
    inner = BOOTSTRAP_INNER_ITER if bootstrap else PRODUCTION_INNER_ITER
    history_stem = f"history_{CASE_NAME}_{start_step:05d}_{end_step:05d}"
    if restart_extension not in {".csv", ".dat"}:
        raise GateFailure(f"unsupported restart extension: {restart_extension}")
    binary_restart = restart_extension == ".dat"
    overrides = {
        "RESTART_SOL": "YES",
        "READ_BINARY_RESTART": "YES" if binary_restart else "NO",
        "DISCARD_INFILES": "NO",
        "TIME_DOMAIN": "YES",
        "TIME_MARCHING": mode,
        "TIME_STEP": f"{PHYSICAL_DT:.8g}",
        "RESTART_ITER": str(start_step),
        "TIME_ITER": str(end_step + 1),
        "INNER_ITER": str(inner),
        "MESH_FILENAME": str((repo_root / "meshes" / MESH_BASENAME).resolve()),
        "SOLUTION_FILENAME": RESTART_STEM,
        "RESTART_FILENAME": RESTART_STEM,
        "CONV_FILENAME": history_stem,
        "VOLUME_FILENAME": f"flow_{CASE_NAME}",
        "SURFACE_FILENAME": f"surface_{CASE_NAME}",
        "TABULAR_FORMAT": "CSV",
        "SCREEN_WRT_FREQ_INNER": "50",
        "OUTPUT_WRT_FREQ": "1",
        "OUTPUT_FILES": "(RESTART)" if binary_restart else "(RESTART_ASCII)",
        "SCREEN_OUTPUT": "(TIME_ITER, INNER_ITER, RMS_DENSITY, RMS_TKE, RMS_DISSIPATION, LIFT, DRAG)",
        "HISTORY_OUTPUT": "(TIME_ITER, INNER_ITER, RMS_RES, AERO_COEFF, AERO_COEFF_SURF)",
    }
    # ITER is a steady/legacy control and SU2 8.5 rejects it in unsteady mode.
    rendered = cfg_with_overrides(base_text, overrides, remove_keys={"ITER"})
    validate_science(parse_cfg_text(rendered), "generated Unity config")
    return rendered, history_stem


def handle_stop(signum: int, _frame: Any) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True
    print(f"# received signal {signum}; preserving last completed restart", flush=True)
    if ACTIVE_SOLVER is not None and ACTIVE_SOLVER.poll() is None:
        ACTIVE_SOLVER.send_signal(signal.SIGINT)


def run_solver(
    solver: Path,
    threads: int,
    cfg_path: Path,
    run_root: Path,
    log_path: Path,
) -> int:
    global ACTIVE_SOLVER
    command = [str(solver), "-t", str(threads), str(cfg_path.resolve())]
    with log_path.open("w", encoding="utf-8", newline="") as log:
        log.write(f"# started_utc: {utc_now()}\n")
        log.write(f"# command: {' '.join(command)}\n")
        log.flush()
        ACTIVE_SOLVER = subprocess.Popen(
            command,
            cwd=run_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
        )
        assert ACTIVE_SOLVER.stdout is not None
        for line in ACTIVE_SOLVER.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()
        ACTIVE_SOLVER.stdout.close()
        returncode = ACTIVE_SOLVER.wait()
        ACTIVE_SOLVER = None
    return returncode


def package_checkpoint(
    checkpoint_dir: Path,
    run_root: Path,
    step: int,
    manifest: dict[str, Any],
    restart_extension: str = ".csv",
) -> Path:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = checkpoint_dir / (
        f"URANS_alpha40_{CASE_NAME}_checkpoint_t{step:06d}.zip"
    )
    temporary = checkpoint.with_suffix(".zip.tmp")
    include: list[Path] = []
    for level in (max(0, step - 1), step):
        path = restart_path(run_root, level, restart_extension)
        if path.is_file():
            include.append(path)
    include.extend(sorted((run_root / "configs").glob("*.cfg"))[-2:])
    include.extend(sorted((run_root / "logs").glob("*.log"))[-2:])
    include.extend(sorted((run_root / "histories").glob("*.csv"))[-2:])
    include.extend(
        path
        for path in (
            run_root / "seed" / "seed_original.cfg",
            run_root / "seed" / "seed_manifest.json",
            run_root / "seed" / "resume_checkpoint_manifest.json",
            run_root / "status.json",
        )
        if path.is_file()
    )
    include = list(dict.fromkeys(include))
    payload = dict(manifest)
    payload["checkpoint_created_utc"] = utc_now()
    payload["checkpoint_file"] = checkpoint.name
    payload["included_sha256"] = {
        str(path.relative_to(run_root)): sha256_file(path) for path in include
    }
    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for path in include:
            archive.write(path, arcname=str(path.relative_to(run_root)))
        archive.writestr(
            "checkpoint_manifest.json",
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )
    os.replace(temporary, checkpoint)
    for older in checkpoint_dir.glob(
        f"URANS_alpha40_{CASE_NAME}_checkpoint_t*.zip"
    ):
        if older != checkpoint:
            older.unlink()
    return checkpoint


def prune_restart_levels(
    run_root: Path, step: int, restart_extension: str
) -> tuple[int, int]:
    keep = {max(0, step - 1), step}
    removed_files = 0
    removed_bytes = 0
    for path in run_root.glob(f"{RESTART_STEM}_*{restart_extension}"):
        match = RESTART_RE.match(path.name)
        if not match or int(match.group(1)) in keep:
            continue
        removed_bytes += path.stat().st_size
        path.unlink()
        removed_files += 1
    return removed_files, removed_bytes


def write_status(
    run_root: Path,
    status: str,
    step: int,
    metrics: dict[str, Any] | None,
    nonphysical: int,
    message: str,
) -> dict[str, Any]:
    payload = {
        "updated_utc": utc_now(),
        "status": status,
        "qualification": "NOT_QUALIFIED",
        "case": CASE_NAME,
        "time_step": step,
        "dt_seconds": PHYSICAL_DT,
        "target_time_step": DEFAULT_TARGET_STEP,
        "metrics": metrics,
        "nonphysical_points": nonphysical,
        "message": message,
    }
    write_json(run_root / "status.json", payload)
    return payload


def run(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    run_root = args.run_root.resolve()
    checkpoint_dir = args.checkpoint_dir.resolve()
    seed_zip = args.seed.resolve()
    resume_checkpoint = (
        args.resume_checkpoint.resolve() if args.resume_checkpoint else None
    )
    solver = args.solver.resolve()
    if not solver.is_file() or not os.access(solver, os.X_OK):
        raise GateFailure(f"SU2_CFD is not executable: {solver}")
    if args.target_step != DEFAULT_TARGET_STEP:
        raise GateFailure(
            f"target step is protocol-locked to {DEFAULT_TARGET_STEP}; "
            "do not override it for this stage"
        )
    if args.chunk_steps < 1:
        raise GateFailure("chunk steps must be positive")

    run_root.mkdir(parents=True, exist_ok=True)
    lock_path = run_root / ".runner.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GateFailure("another alpha40 Unity runner already holds the lock") from exc
        lock.write(f"pid={os.getpid()} started={utc_now()}\n")
        lock.flush()

        for directory in ("configs", "logs", "histories", "seed"):
            (run_root / directory).mkdir(exist_ok=True)
        seed_manifest_path = run_root / "seed" / "seed_manifest.json"
        if not seed_manifest_path.is_file():
            if resume_checkpoint is not None:
                restored = restore_checkpoint(
                    resume_checkpoint, seed_zip, repo_root, run_root
                )
                print(
                    f"# restored audited checkpoint at step {restored['time_step']}",
                    flush=True,
                )
            else:
                prepare_seed(seed_zip, repo_root, run_root)
        else:
            current = inspect_seed(seed_zip)
            recorded = json.loads(seed_manifest_path.read_text(encoding="utf-8"))
            if current["seed_sha256"] != recorded.get("seed_sha256"):
                raise GateFailure("seed ZIP changed after the run directory was initialized")
            validate_mesh(repo_root)

        seed_manifest = json.loads(seed_manifest_path.read_text(encoding="utf-8"))
        restart_extension = seed_manifest.get("restart_extension")
        if restart_extension not in {".csv", ".dat"}:
            raise GateFailure("seed manifest has no supported restart extension")

        base_text = (run_root / "seed" / "seed_original.cfg").read_text(
            encoding="utf-8"
        )
        latest = latest_restart_step(run_root)
        if latest is None:
            raise GateFailure("no restart level exists after seed preparation")
        if latest >= args.target_step:
            status = write_status(
                run_root,
                "CHECKPOINTED",
                latest,
                None,
                0,
                "medium_halfdt target reached; matrix qualification is still pending",
            )
            package_checkpoint(
                checkpoint_dir, run_root, latest, status, restart_extension
            )
            prune_restart_levels(run_root, latest, restart_extension)
            return 0

        while latest < args.target_step:
            start = latest + 1
            require_restart_levels(run_root, start, restart_extension)
            if start == 1:
                end = 1
            elif start <= 4:
                end = 4
            else:
                end = min(args.target_step, start + args.chunk_steps - 1)
            rendered, history_stem = make_config(
                base_text, repo_root, start, end, restart_extension
            )
            cfg_path = run_root / "configs" / f"{CASE_NAME}_{start:05d}_{end:05d}.cfg"
            cfg_path.write_text(rendered, encoding="utf-8")
            log_path = run_root / "logs" / f"{CASE_NAME}_{start:05d}_{end:05d}.log"
            write_status(
                run_root,
                "RUNNING",
                latest,
                None,
                0,
                f"running steps {start}-{end}",
            )
            returncode = run_solver(
                solver, args.threads, cfg_path, run_root, log_path
            )
            history_source = run_root / f"{history_stem}.csv"
            history_archive = run_root / "histories" / history_source.name
            if history_source.is_file():
                shutil.copy2(history_source, history_archive)

            new_latest = latest_restart_step(run_root)
            if new_latest is None:
                new_latest = latest
            logs = sorted((run_root / "logs").glob("*.log"))
            nonphysical, warning_lines = nonphysical_count(logs)
            metrics: dict[str, Any] | None = None
            if history_archive.is_file():
                metrics = last_metrics(history_archive)
            failure: str | None = None
            if nonphysical != 0:
                failure = f"nonphysical points reported: {nonphysical}"
            elif returncode != 0 and not STOP_REQUESTED:
                failure = f"SU2_CFD exited with code {returncode}"
            elif new_latest < latest:
                failure = "restart sequence moved backward"
            elif not STOP_REQUESTED and new_latest < end:
                failure = f"expected completed step {end}, found {new_latest}"

            status_label = "FAILED_GATE" if failure else "CHECKPOINTED"
            message = failure or (
                "walltime signal checkpoint" if STOP_REQUESTED else "chunk completed"
            )
            status = write_status(
                run_root,
                status_label,
                new_latest,
                metrics,
                nonphysical,
                message,
            )
            status["nonphysical_log_lines"] = warning_lines[-20:]
            checkpoint = package_checkpoint(
                checkpoint_dir, run_root, new_latest, status, restart_extension
            )
            removed_files, removed_bytes = prune_restart_levels(
                run_root, new_latest, restart_extension
            )
            print(
                json.dumps(
                    {
                        "status": status_label,
                        "time_step": new_latest,
                        "metrics": metrics,
                        "nonphysical_points": nonphysical,
                        "checkpoint": str(checkpoint),
                        "pruned_restart_files": removed_files,
                        "pruned_restart_bytes": removed_bytes,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if failure:
                raise GateFailure(failure)
            if STOP_REQUESTED:
                return 75
            latest = new_latest
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    preflight = sub.add_parser("preflight", help="validate seed and mesh only")
    preflight.add_argument("--seed", type=Path, required=True)
    preflight.add_argument("--repo-root", type=Path, required=True)
    preflight.add_argument("--resume-checkpoint", type=Path)

    assemble = sub.add_parser(
        "assemble-resume", help="reassemble the bundled resume archive"
    )
    assemble.add_argument("--parts-dir", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)

    execute = sub.add_parser("run", help="run/restart medium_halfdt on Unity")
    execute.add_argument("--seed", type=Path, required=True)
    execute.add_argument("--repo-root", type=Path, required=True)
    execute.add_argument("--run-root", type=Path, required=True)
    execute.add_argument("--checkpoint-dir", type=Path, required=True)
    execute.add_argument("--resume-checkpoint", type=Path)
    execute.add_argument("--solver", type=Path, required=True)
    execute.add_argument("--threads", type=int, required=True)
    execute.add_argument("--target-step", type=int, default=DEFAULT_TARGET_STEP)
    execute.add_argument("--chunk-steps", type=int, default=DEFAULT_CHUNK_STEPS)

    status = sub.add_parser("status", help="print the latest machine-readable state")
    status.add_argument("--run-root", type=Path, required=True)
    return parser


def main() -> int:
    signal.signal(signal.SIGUSR1, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    args = build_parser().parse_args()
    try:
        if args.command == "preflight":
            payload = {
                "status": "PREPARING",
                "qualification": "NOT_QUALIFIED",
                **inspect_seed(args.seed.resolve()),
                **validate_mesh(args.repo_root.resolve()),
            }
            if args.resume_checkpoint:
                payload["resume_checkpoint"] = inspect_resume_checkpoint(
                    args.resume_checkpoint.resolve(), args.seed.resolve()
                )
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if args.command == "status":
            path = args.run_root.resolve() / "status.json"
            if not path.is_file():
                raise GateFailure(f"no status file yet: {path}")
            print(path.read_text(encoding="utf-8"), end="")
            return 0
        if args.command == "assemble-resume":
            payload = assemble_resume_parts(
                args.parts_dir.resolve(), args.output.resolve()
            )
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if args.threads < 1:
            raise GateFailure("threads must be positive")
        return run(args)
    except (GateFailure, zipfile.BadZipFile) as exc:
        print(f"FAILED_GATE: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
