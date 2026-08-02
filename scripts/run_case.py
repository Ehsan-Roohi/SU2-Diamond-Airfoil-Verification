#!/usr/bin/env python3
"""Run a Chapter 6 SU2 case and apply fail-closed numerical checks.

SU2's process return code is not treated as proof of convergence.  Production
runs must have the requested number of force samples and pass all configured
checks.  Reference-value checks are activated only when their cells in
EXPECTED_RESULTS.csv contain numeric values; the distributed table deliberately
does not invent unarchived targets.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPECTED_TABLE = ROOT / "EXPECTED_RESULTS.csv"
NONPHYSICAL_TOKEN = re.compile(r"\bnon[- ]?physical\b", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run SU2 stages with fail-closed production checks."
    )
    parser.add_argument("case", nargs="?", default=".", help="case directory")
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument(
        "--threads", "-t", type=int, default=4,
        help="OpenMP threads passed as 'SU2_CFD -t N' (default: 4)",
    )
    execution.add_argument(
        "--mpi", type=int, metavar="N",
        help="MPI ranks; use only with an MPI-enabled SU2_CFD executable",
    )
    parser.add_argument(
        "--archive-old", action="store_true",
        help="move pre-existing outputs to previous_outputs/ instead of stopping",
    )
    parser.add_argument(
        "--smoke", action="store_true",
        help="installation-only policy: require launch/output but not convergence",
    )
    parser.add_argument(
        "--window", type=int,
        help="override the production force-window length",
    )
    parser.add_argument(
        "--load-tolerance-percent", type=float,
        help="override relative peak-to-peak tolerance for CD and nonzero-alpha CL",
    )
    parser.add_argument(
        "--cl-absolute-tolerance", type=float,
        help="override absolute CL peak-to-peak tolerance at alpha=0 degrees",
    )
    parser.add_argument(
        "--expected-results", type=Path, default=DEFAULT_EXPECTED_TABLE,
        help="acceptance table (default: package EXPECTED_RESULTS.csv)",
    )
    parser.add_argument(
        "--metrics-file", type=Path,
        help="post-processing metrics JSON (default: case/case_metrics.json)",
    )
    return parser.parse_args()


def parse_cfg(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("%", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().upper()] = value.strip()
    return values


def output_path(case: Path, stem: str, suffix: str) -> Path:
    candidate = case / stem
    return candidate if candidate.suffix else candidate.with_suffix(suffix)


def existing_stage_outputs(case: Path, cfg: dict[str, str]) -> list[Path]:
    prefixes = []
    for key in ("CONV_FILENAME", "RESTART_FILENAME", "VOLUME_FILENAME", "SURFACE_FILENAME"):
        if key in cfg:
            prefixes.append(Path(cfg[key]).name)
    found: set[Path] = set()
    for prefix in prefixes:
        for path in case.glob(prefix + "*"):
            if path.is_file():
                found.add(path)
    return sorted(found)


def archive_outputs(case: Path, paths: list[Path], stamp: str) -> Path:
    destination = case / "previous_outputs" / stamp
    destination.mkdir(parents=True, exist_ok=True)
    for path in paths:
        shutil.move(str(path), destination / path.name)
    return destination


def tee_process(command: list[str], cwd: Path, log_path: Path) -> int:
    with log_path.open("w", encoding="utf-8", newline="") as log:
        log.write(f"# started_utc: {dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        log.write(f"# cwd: {cwd}\n")
        log.write(f"# command: {' '.join(command)}\n")
        log.flush()
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            log.write(line)
        return process.wait()


def normalized_header(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def find_column(fieldnames: list[str], candidates: tuple[str, ...]) -> str | None:
    normalized = {normalized_header(name): name for name in fieldnames}
    for candidate in candidates:
        compact = normalized_header(candidate)
        if compact in normalized:
            return normalized[compact]
    for clean, original in normalized.items():
        if any(normalized_header(candidate) in clean for candidate in candidates):
            return original
    return None


def read_history(path: Path) -> tuple[list[dict[str, str]], dict[str, str | None]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"No data rows in {path.name}")
    fields = list(rows[0])
    columns = {
        "iteration": find_column(fields, ("INNER_ITER", "ITER", "TIME_ITER")),
        "residual": find_column(fields, ("RMS_DENSITY", "RMS[RHO]", "RMSRHO")),
        "cl": find_column(fields, ("LIFT", "CL")),
        "cd": find_column(fields, ("DRAG", "CD")),
    }
    return rows, columns


def finite_values(rows: list[dict[str, str]], key: str | None) -> list[float]:
    if key is None:
        return []
    values: list[float] = []
    for row in rows:
        try:
            value = float(row[key])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def nonphysical_counts(log_path: Path) -> tuple[int, int, list[str]]:
    """Return maxima for SU2 point and reconstructed-state warnings.

    Real SU2 releases use all of ``nonphysical``, ``non-physical``, and
    occasionally ``non physical``.  Matching lines are retained in the
    manifest so the extraction remains auditable.
    """
    point_max = 0
    reconstructed_max = 0
    matches: list[str] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        lower = line.lower()
        if NONPHYSICAL_TOKEN.search(lower) is None:
            continue
        matches.append(line.strip())
        nearby = re.search(r"(\d+)\s+non[- ]?physical", lower)
        if nearby is None:
            nearby = re.search(r"non[- ]?physical[^0-9]{0,40}(\d+)", lower)
        if nearby is None:
            numbers = [int(item) for item in re.findall(r"\b\d+\b", line)]
            count = numbers[-1] if numbers else 1
        else:
            count = int(nearby.group(1))
        if "reconstruct" in lower or "state" in lower:
            reconstructed_max = max(reconstructed_max, count)
        else:
            point_max = max(point_max, count)
    return point_max, reconstructed_max, matches


def read_expected(case_name: str, table: Path) -> dict[str, str]:
    if not table.exists():
        return {}
    with table.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("case") == case_name:
                return {key: (value or "").strip() for key, value in row.items()}
    return {}


def optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.upper() in {
        "TBD", "N/A", "NA", "NOT_APPLICABLE", "HARDWARE_DEPENDENT",
    }:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def as_float(value: str | None, default: float) -> float:
    parsed = optional_float(value)
    return default if parsed is None else parsed


def as_int(value: str | None, default: int) -> int:
    parsed = optional_float(value)
    return default if parsed is None else int(parsed)


def force_stats(values: list[float], window: int) -> dict[str, float | int] | None:
    if not values:
        return None
    sample = values[-min(window, len(values)):]
    mean = sum(sample) / len(sample)
    peak_to_peak = max(sample) - min(sample)
    relative_percent = 100.0 * peak_to_peak / max(abs(mean), 1.0e-300)
    return {
        "final": sample[-1],
        "mean": mean,
        "peak_to_peak": peak_to_peak,
        "peak_to_peak_percent": relative_percent,
        "samples": len(sample),
        "available_samples": len(values),
    }


def read_metrics(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("metrics JSON must contain an object")
    return data


def auto_extract_density_metrics(
    case: Path,
    final_cfg: dict[str, str],
    expected: dict[str, str],
    metrics_path: Path,
    need_symmetry: bool,
    need_shock: bool,
) -> tuple[int, str]:
    """Run the packaged native-grid extractor for the just-created restart."""
    restart = output_path(
        case, final_cfg.get("RESTART_FILENAME", "restart_second_order"), ".csv"
    )
    mesh_name = final_cfg.get("MESH_FILENAME")
    if not mesh_name:
        return 2, "final configuration has no MESH_FILENAME"
    mesh = (case / mesh_name).resolve()
    command = [
        sys.executable,
        str(ROOT / "scripts" / "extract_wave_metrics.py"),
        str(restart),
        "--mesh",
        str(mesh),
        "--metrics-json",
        str(metrics_path),
    ]
    if need_shock:
        reference = optional_float(expected.get("shock_angle_reference_deg"))
        if reference is None:
            return 2, "shock-angle tolerance is populated but shock_angle_reference_deg is not"
        branch = (expected.get("shock_branch") or "upper").strip().lower()
        if branch not in {"upper", "lower"}:
            return 2, f"invalid shock_branch '{branch}'"
        command.extend(["--branch", branch, "--reference-angle-deg", str(reference)])
        x_min = optional_float(expected.get("shock_fit_x_min"))
        x_max = optional_float(expected.get("shock_fit_x_max"))
        if x_min is not None:
            command.extend(["--x-min", str(x_min)])
        if x_max is not None:
            command.extend(["--x-max", str(x_max)])
    else:
        command.append("--skip-shock")
    if need_symmetry:
        command.append("--symmetry")
    process = subprocess.run(
        command,
        cwd=case,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    return process.returncode, process.stdout


def numeric_metric(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def check_reference_range(
    label: str,
    value: float,
    minimum: float | None,
    maximum: float | None,
    failures: list[str],
) -> dict[str, float | bool | None]:
    passed = True
    if minimum is not None and value < minimum:
        failures.append(f"{label} final-window mean {value:.8g} is below {minimum:.8g}")
        passed = False
    if maximum is not None and value > maximum:
        failures.append(f"{label} final-window mean {value:.8g} is above {maximum:.8g}")
        passed = False
    return {"value": value, "minimum": minimum, "maximum": maximum, "passed": passed}


def main() -> int:
    args = parse_args()
    if args.threads is not None and args.threads < 1:
        raise SystemExit("--threads must be at least 1")
    if args.mpi is not None and args.mpi < 1:
        raise SystemExit("--mpi must be at least 1")
    if args.window is not None and args.window < 1:
        raise SystemExit("--window must be at least 1")
    if args.load_tolerance_percent is not None and args.load_tolerance_percent <= 0.0:
        raise SystemExit("--load-tolerance-percent must be positive")
    if args.cl_absolute_tolerance is not None and args.cl_absolute_tolerance <= 0.0:
        raise SystemExit("--cl-absolute-tolerance must be positive")

    case = Path(args.case).resolve()
    if not case.is_dir():
        raise SystemExit(f"Case directory not found: {case}")
    exe = shutil.which("SU2_CFD")
    if not exe:
        raise SystemExit("SU2_CFD was not found on PATH. See README.md section 1.")

    configs = ["smoke_test.cfg"] if args.smoke else ["startup.cfg", "second_order.cfg"]
    for filename in configs:
        if not (case / filename).is_file():
            raise SystemExit(f"Missing {filename} in {case}")

    if args.mpi is not None:
        launcher = shutil.which("mpirun") or shutil.which("mpiexec")
        if not launcher:
            raise SystemExit("MPI launcher not found. Use --threads with the OMP package.")
        base_command = [launcher, "-np", str(args.mpi), exe]
        execution = {"mode": "MPI", "workers": args.mpi, "launcher": launcher}
    else:
        base_command = [exe, "-t", str(args.threads)]
        execution = {"mode": "OpenMP", "workers": args.threads}

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logs = case / "logs"
    logs.mkdir(exist_ok=True)
    expected_table = args.expected_results.resolve()
    expected = read_expected(case.name, expected_table)
    manifest: dict[str, object] = {
        "case": case.name,
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "solver_executable": exe,
        "reference_solver": "SU2 v8.5.0 (Harrier)",
        "execution": execution,
        "policy": "INSTALLATION_ONLY_SMOKE" if args.smoke else "PRODUCTION_FAIL_CLOSED",
        "expected_results_table": str(expected_table),
        "reference_status": expected.get("reference_status", "NOT_LISTED"),
        "stages": [],
    }

    failures: list[str] = []
    acceptance_warnings: list[str] = []
    smoke_warnings: list[str] = []
    final_rows: list[dict[str, str]] | None = None
    final_columns: dict[str, str | None] | None = None
    final_cfg: dict[str, str] | None = None

    for filename in configs:
        cfg_path = case / filename
        cfg = parse_cfg(cfg_path)
        stale = existing_stage_outputs(case, cfg)
        if stale:
            if not args.archive_old:
                names = ", ".join(path.name for path in stale[:8])
                raise SystemExit(
                    f"Refusing to mix old and new outputs ({names}). "
                    "Use --archive-old to move them aside."
                )
            destination = archive_outputs(case, stale, stamp + "_" + cfg_path.stem)
            print(f"Archived {len(stale)} old output(s) to {destination}")

        log_path = logs / f"{stamp}_{cfg_path.stem}.log"
        command = base_command + [filename]
        print(f"\nRunning {case.name}: {filename}")
        print(f"Command: {' '.join(command)}")
        return_code = tee_process(command, case, log_path)

        history = output_path(case, cfg.get("CONV_FILENAME", "history"), ".csv")
        restart = output_path(case, cfg.get("RESTART_FILENAME", "restart"), ".csv")
        stage_record: dict[str, object] = {
            "config": filename,
            "command": command,
            "return_code": return_code,
            "log": str(log_path.relative_to(case)),
            "history": history.name,
            "restart": restart.name,
        }
        if return_code != 0:
            failures.append(f"{filename}: SU2 returned {return_code}")
        if not history.is_file():
            failures.append(f"{filename}: missing {history.name}")
        if not restart.is_file():
            failures.append(f"{filename}: missing {restart.name}")

        point_max, reconstructed_max, warning_lines = nonphysical_counts(log_path)
        stage_record["nonphysical_points_max_reported"] = point_max
        stage_record["nonphysical_reconstructed_states_max_reported"] = reconstructed_max
        stage_record["nonphysical_log_lines"] = warning_lines
        manifest["stages"].append(stage_record)  # type: ignore[union-attr]

        allowed_nonphysical = as_int(expected.get("max_nonphysical_points"), 0)
        if args.smoke:
            if warning_lines:
                smoke_warnings.append(
                    f"{filename}: recorded nonphysical warnings "
                    f"(points={point_max}, reconstructed={reconstructed_max}); "
                    "ignored only by installation-smoke policy"
                )
        elif point_max > allowed_nonphysical or reconstructed_max > allowed_nonphysical:
            failures.append(
                f"{filename}: nonphysical-state warning exceeds allowed "
                f"{allowed_nonphysical} (points={point_max}, reconstructed={reconstructed_max})"
            )

        if failures:
            break
        if history.is_file():
            try:
                final_rows, final_columns = read_history(history)
            except ValueError as exc:
                failures.append(str(exc))
                break
        final_cfg = cfg

    if args.smoke:
        if final_rows is None:
            failures.append("smoke history contains no readable data rows")
        manifest["scope"] = (
            "installation/output smoke test only; nonphysical warnings are recorded "
            "but do not define physical acceptance"
        )
        manifest["warnings"] = smoke_warnings
        manifest["result"] = "PASS" if not failures else "FAIL"
    elif not failures and final_rows is not None and final_columns is not None and final_cfg is not None:
        residuals = finite_values(final_rows, final_columns["residual"])
        final_residual: float | None
        initial_residual: float | None
        residual_drop_orders: float | None
        target = as_float(
            expected.get("residual_target"),
            as_float(final_cfg.get("CONV_RESIDUAL_MINVAL"), -10.0),
        )
        residual_policy = (expected.get("residual_policy") or "enforce").strip().lower()
        if residual_policy not in {"enforce", "warning"}:
            failures.append(
                f"unknown residual_policy '{residual_policy}'; use enforce or warning"
            )
            residual_policy = "enforce"

        def residual_issue(message: str) -> None:
            if residual_policy == "warning":
                acceptance_warnings.append(message)
            else:
                failures.append(message)

        if not residuals:
            failures.append("final history has no readable density-residual column")
            initial_residual = None
            final_residual = None
            residual_drop_orders = None
        else:
            initial_residual = residuals[0]
            final_residual = residuals[-1]
            residual_drop_orders = initial_residual - final_residual
            if final_residual > target:
                residual_issue(
                    f"final density residual {final_residual:.6g} is above target {target:g}"
                )
            minimum_drop = optional_float(expected.get("residual_drop_min_orders"))
            if minimum_drop is not None and residual_drop_orders < minimum_drop:
                residual_issue(
                    f"density-residual reduction {residual_drop_orders:.6g} orders is below "
                    f"configured minimum {minimum_drop:g}"
                )

        window = args.window or as_int(expected.get("load_window"), 200)
        if window < 1:
            failures.append("configured load_window must be at least 1")
            window = 200
        relative_tolerance = (
            args.load_tolerance_percent
            if args.load_tolerance_percent is not None
            else as_float(expected.get("load_ptp_limit_pct"), 1.0)
        )
        if relative_tolerance <= 0.0:
            failures.append("configured load_ptp_limit_pct must be positive")
        alpha = as_float(final_cfg.get("AOA"), float("nan"))
        zero_alpha = math.isfinite(alpha) and abs(alpha) < 1.0e-12
        cl_abs_limit = (
            args.cl_absolute_tolerance
            if args.cl_absolute_tolerance is not None
            else optional_float(expected.get("cl_ptp_abs_limit"))
        )

        stats: dict[str, dict[str, float | int] | None] = {}
        for label, column in (("CL", final_columns["cl"]), ("CD", final_columns["cd"])):
            result = force_stats(finite_values(final_rows, column), window)
            stats[label] = result
            if result is None:
                failures.append(f"final history has no readable {label} column")
                continue
            if int(result["available_samples"]) < window:
                failures.append(
                    f"{label} has only {result['available_samples']} readable samples; "
                    f"production acceptance requires at least {window}"
                )
                continue
            if label == "CL" and zero_alpha:
                if cl_abs_limit is None:
                    failures.append(
                        "alpha=0 requires an absolute CL peak-to-peak limit: populate "
                        "cl_ptp_abs_limit or pass --cl-absolute-tolerance"
                    )
                elif cl_abs_limit <= 0.0:
                    failures.append("configured cl_ptp_abs_limit must be positive")
                elif float(result["peak_to_peak"]) > cl_abs_limit:
                    failures.append(
                        f"CL final-window absolute peak-to-peak {result['peak_to_peak']:.6g} "
                        f"exceeds {cl_abs_limit:g} at alpha=0"
                    )
            elif float(result["peak_to_peak_percent"]) > relative_tolerance:
                failures.append(
                    f"{label} final-window peak-to-peak "
                    f"{result['peak_to_peak_percent']:.3g}% exceeds {relative_tolerance:g}%"
                )

        range_checks: dict[str, object] = {}
        for label, min_key, max_key in (
            ("CL", "cl_min", "cl_max"),
            ("CD", "cd_min", "cd_max"),
        ):
            result = stats.get(label)
            minimum = optional_float(expected.get(min_key))
            maximum = optional_float(expected.get(max_key))
            if result is not None and (minimum is not None or maximum is not None):
                range_checks[label] = check_reference_range(
                    label, float(result["mean"]), minimum, maximum, failures
                )

        optional_limits = {
            "symmetry_error_rms": optional_float(expected.get("symmetry_tolerance")),
            "shock_angle_error_deg": optional_float(
                expected.get("shock_angle_tolerance_deg")
            ),
            "yplus_max": optional_float(expected.get("yplus_target")),
        }
        active_optional = {key: value for key, value in optional_limits.items() if value is not None}
        metrics_checks: dict[str, object] = {}
        if active_optional:
            if args.metrics_file is not None:
                metrics_path = (
                    args.metrics_file
                    if args.metrics_file.is_absolute()
                    else case / args.metrics_file
                ).resolve()
            else:
                metrics_path = case / (expected.get("metrics_file") or "case_metrics.json")
            need_symmetry = "symmetry_error_rms" in active_optional
            need_shock = "shock_angle_error_deg" in active_optional
            if need_symmetry or need_shock:
                if metrics_path.exists():
                    archived_metrics = logs / f"{stamp}_preexisting_{metrics_path.name}"
                    shutil.move(str(metrics_path), archived_metrics)
                metric_return, metric_output = auto_extract_density_metrics(
                    case,
                    final_cfg,
                    expected,
                    metrics_path,
                    need_symmetry,
                    need_shock,
                )
                print("\nNative-grid metric extraction:")
                print(metric_output.rstrip())
                manifest["metric_extractor_return_code"] = metric_return
                manifest["metric_extractor_output"] = metric_output
                if metric_return != 0:
                    failures.append(
                        f"native-grid metric extraction returned {metric_return}"
                    )
            if not metrics_path.is_file():
                failures.append(
                    f"configured post-processing limits require metrics file {metrics_path}"
                )
            else:
                try:
                    metrics = read_metrics(metrics_path)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    failures.append(f"cannot read metrics file {metrics_path}: {exc}")
                else:
                    manifest["metrics_file"] = str(metrics_path)
                    for key, limit in active_optional.items():
                        observed = numeric_metric(metrics, key)
                        passed = observed is not None and observed <= float(limit)
                        metrics_checks[key] = {
                            "value": observed,
                            "maximum": limit,
                            "passed": passed,
                        }
                        if observed is None:
                            failures.append(
                                f"metrics file does not contain finite numeric '{key}'"
                            )
                        elif observed > float(limit):
                            failures.append(
                                f"{key} {observed:.8g} exceeds configured limit {limit:.8g}"
                            )

        manifest["acceptance_checks"] = {
            "angle_of_attack_deg": alpha,
            "initial_density_residual": initial_residual,
            "final_density_residual": final_residual,
            "density_residual_drop_orders": residual_drop_orders,
            "residual_target": target,
            "residual_policy": residual_policy,
            "residual_drop_min_orders": optional_float(
                expected.get("residual_drop_min_orders")
            ),
            "force_window_required_samples": window,
            "force_peak_to_peak_limit_percent": relative_tolerance,
            "cl_peak_to_peak_absolute_limit_at_alpha0": cl_abs_limit,
            "force_statistics": stats,
            "reference_range_checks": range_checks,
            "postprocessing_metric_checks": metrics_checks,
            "max_nonphysical_points": as_int(
                expected.get("max_nonphysical_points"), 0
            ),
        }
        manifest["result"] = (
            "FAIL" if failures else ("QUALIFIED_PASS" if acceptance_warnings else "PASS")
        )
        manifest["warnings"] = acceptance_warnings
        manifest["reference_note"] = (
            "PASS means configured automated checks passed. It is not a published-reference "
            "match unless EXPECTED_RESULTS.csv says VERIFIED and supplies traceable ranges."
        )
    else:
        manifest["result"] = "FAIL"

    manifest["failures"] = failures
    manifest["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest_path = logs / f"{stamp}_run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"\nRun manifest: {manifest_path}")
    if smoke_warnings:
        print("Smoke-test warnings (recorded; installation policy only):")
        for warning in smoke_warnings:
            print(f"  - {warning}")
    if acceptance_warnings:
        print("Qualified acceptance warnings:")
        for warning in acceptance_warnings:
            print(f"  - {warning}")
    if failures:
        print("FAIL — do not use this run as an accepted result:")
        for failure in failures:
            print(f"  - {failure}")
        return 2

    if args.smoke:
        print("PASS — SU2 launched and wrote the expected smoke-test files.")
        print("This policy does not assess convergence, forces, or physical validity.")
    else:
        if acceptance_warnings:
            print(
                "QUALIFIED PASS — force, physicality, symmetry/wave, and configured "
                "reference checks passed; residual warnings remain explicit."
            )
        else:
            print(
                "PASS — required files, residual, 200-sample force window, and all "
                "configured acceptance checks passed."
            )
        if expected.get("reference_status") not in {
            "VERIFIED", "QUALIFIED_TEACHING_REFERENCE_NOT_GRID_BENCHMARK"
        }:
            print("REFERENCE MATCH PENDING — verified package-specific ranges are not archived.")
        elif expected.get("reference_status") == "QUALIFIED_TEACHING_REFERENCE_NOT_GRID_BENCHMARK":
            print("QUALIFIED TEACHING REFERENCE — not a grid-converged benchmark.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
