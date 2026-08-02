#!/usr/bin/env python3
"""Regression tests for the fail-closed wrapper (no SU2 installation needed)."""

from __future__ import annotations

import json
import csv
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "run_case.py"


CFG = """\
AOA= {alpha}
CONV_FILENAME= history_{stage}
RESTART_FILENAME= restart_{stage}
CONV_RESIDUAL_MINVAL= -10
"""


FAKE_SOLVER = r'''#!/usr/bin/env python3
import os
import re
import sys
from pathlib import Path

cfg_path = Path(sys.argv[-1])
text = cfg_path.read_text()
def value(key):
    return re.search(rf"^{key}\s*=\s*(\S+)", text, re.M).group(1)
history = Path(value("CONV_FILENAME") + ".csv")
restart = Path(value("RESTART_FILENAME") + ".csv")
residual = float(os.environ.get("FAKE_RESIDUAL", "-11"))
initial_residual = float(os.environ.get("FAKE_RESIDUAL_INITIAL", str(residual)))
rows = int(os.environ.get("FAKE_ROWS", "250"))
cl = float(os.environ.get("FAKE_CL", "0.1"))
cd = float(os.environ.get("FAKE_CD", "0.03"))
cl_swing = float(os.environ.get("FAKE_CL_SWING", "0"))
cd_swing = float(os.environ.get("FAKE_CD_SWING", "0"))
with history.open("w") as handle:
    handle.write('"Inner_Iter","rms[Rho]","CL","CD"\n')
    for i in range(rows):
        sign = -0.5 if i % 2 == 0 else 0.5
        current_residual = initial_residual if i == 0 else residual
        handle.write(f"{i},{current_residual},{cl + sign*cl_swing},{cd + sign*cd_swing}\n")
restart.write_text("fake restart\n")
count = int(os.environ.get("FAKE_NONPHYSICAL", "0"))
style = os.environ.get("FAKE_WARNING_STYLE", "plain")
if count:
    if style == "real-su2":
        print(f"Warning: there are {count} non-physical points in the solution.")
    elif style == "space":
        print(f"There are {count} non physical reconstructed states.")
    else:
        print(f"There are {count} nonphysical points.")
'''


EXPECTED_FIELDS = (
    "case", "reference_status", "mesh", "cl_min", "cl_max", "cd_min", "cd_max",
    "residual_target", "residual_policy", "residual_drop_min_orders", "load_window",
    "load_ptp_limit_pct", "cl_ptp_abs_limit", "max_nonphysical_points",
    "symmetry_tolerance", "shock_angle_reference_deg", "shock_angle_tolerance_deg",
    "shock_branch", "shock_fit_x_min", "shock_fit_x_max", "yplus_target",
    "metrics_file", "notes",
)


class RunCaseTests(unittest.TestCase):
    def make_case(
        self, root: Path, *, alpha: float = 4.0, smoke: bool = False
    ) -> tuple[Path, dict[str, str]]:
        case = root / "synthetic_case"
        case.mkdir()
        if smoke:
            (case / "smoke_test.cfg").write_text(
                CFG.format(stage="smoke", alpha=alpha), encoding="utf-8"
            )
        else:
            (case / "startup.cfg").write_text(
                CFG.format(stage="startup", alpha=alpha), encoding="utf-8"
            )
            (case / "second_order.cfg").write_text(
                CFG.format(stage="second_order", alpha=alpha), encoding="utf-8"
            )
        bindir = root / "bin"
        bindir.mkdir()
        solver = bindir / "SU2_CFD"
        solver.write_text(FAKE_SOLVER, encoding="utf-8")
        solver.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
        return case, env

    def expected_table(self, root: Path, row: dict[str, str] | None = None) -> Path:
        table = root / "expected.csv"
        with table.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPECTED_FIELDS)
            writer.writeheader()
            if row is not None:
                writer.writerow(row)
        return table

    def run_wrapper(
        self,
        case: Path,
        env: dict[str, str],
        expected: Path,
        *extra: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(WRAPPER),
                str(case),
                "--threads",
                "2",
                "--expected-results",
                str(expected),
                *extra,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )

    def read_manifest(self, case: Path) -> dict[str, object]:
        paths = list((case / "logs").glob("*_run_manifest.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text(encoding="utf-8"))

    def test_clean_stable_run_passes_and_records_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root)
            result = self.run_wrapper(case, env, self.expected_table(root))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("PASS", result.stdout)
            manifest = self.read_manifest(case)
            self.assertEqual(manifest["policy"], "PRODUCTION_FAIL_CLOSED")

    def test_unconverged_zero_exit_solver_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root)
            env["FAKE_RESIDUAL"] = "-3.5"
            result = self.run_wrapper(case, env, self.expected_table(root))
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("above target", result.stdout)

    def test_populated_residual_drop_minimum_is_enforced_and_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root)
            env["FAKE_RESIDUAL_INITIAL"] = "-9.5"
            env["FAKE_RESIDUAL"] = "-11.0"
            row = {
                "case": "synthetic_case", "reference_status": "VERIFIED",
                "mesh": "synthetic", "residual_target": "-10",
                "residual_drop_min_orders": "2.0", "load_window": "200",
                "load_ptp_limit_pct": "1.0", "max_nonphysical_points": "0",
                "notes": "test",
            }
            result = self.run_wrapper(case, env, self.expected_table(root, row))
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("reduction 1.5 orders", result.stdout)
            manifest = self.read_manifest(case)
            checks = manifest["acceptance_checks"]
            self.assertEqual(checks["initial_density_residual"], -9.5)
            self.assertEqual(checks["final_density_residual"], -11.0)
            self.assertEqual(checks["density_residual_drop_orders"], 1.5)

    def test_residual_warning_policy_produces_qualified_pass_not_silent_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root)
            env["FAKE_RESIDUAL_INITIAL"] = "-4.5"
            env["FAKE_RESIDUAL"] = "-4.1"
            row = {
                "case": "synthetic_case",
                "reference_status": "QUALIFIED_TEACHING_REFERENCE_NOT_GRID_BENCHMARK",
                "mesh": "synthetic", "residual_target": "-10",
                "residual_policy": "warning", "residual_drop_min_orders": "0.0",
                "load_window": "200", "load_ptp_limit_pct": "1.0",
                "max_nonphysical_points": "0", "notes": "test",
            }
            result = self.run_wrapper(case, env, self.expected_table(root, row))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("QUALIFIED PASS", result.stdout)
            manifest = self.read_manifest(case)
            self.assertEqual(manifest["result"], "QUALIFIED_PASS")
            self.assertEqual(len(manifest["warnings"]), 2)

    def test_real_su2_hyphenated_nonphysical_warning_is_rejected_and_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root)
            env["FAKE_NONPHYSICAL"] = "47"
            env["FAKE_WARNING_STYLE"] = "real-su2"
            result = self.run_wrapper(case, env, self.expected_table(root))
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("points=47", result.stdout)
            manifest = self.read_manifest(case)
            stage = manifest["stages"][0]
            self.assertEqual(stage["nonphysical_points_max_reported"], 47)
            self.assertIn("non-physical points", stage["nonphysical_log_lines"][0])

    def test_space_separated_reconstructed_warning_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root)
            env["FAKE_NONPHYSICAL"] = "12"
            env["FAKE_WARNING_STYLE"] = "space"
            result = self.run_wrapper(case, env, self.expected_table(root))
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("reconstructed=12", result.stdout)

    def test_smoke_policy_records_warning_but_tests_installation_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root, smoke=True)
            env["FAKE_NONPHYSICAL"] = "9"
            env["FAKE_WARNING_STYLE"] = "real-su2"
            env["FAKE_ROWS"] = "1"
            env["FAKE_RESIDUAL"] = "-1"
            result = self.run_wrapper(
                case, env, self.expected_table(root), "--smoke"
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("installation policy only", result.stdout)
            manifest = self.read_manifest(case)
            self.assertEqual(manifest["policy"], "INSTALLATION_ONLY_SMOKE")
            self.assertTrue(manifest["warnings"])

    def test_production_requires_full_200_sample_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root)
            env["FAKE_ROWS"] = "199"
            result = self.run_wrapper(case, env, self.expected_table(root))
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("only 199 readable samples", result.stdout)

    def test_alpha_zero_uses_absolute_cl_peak_to_peak_tolerance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root, alpha=0.0)
            env["FAKE_CL"] = "0.000001"
            env["FAKE_CL_SWING"] = "0.00008"
            table = self.expected_table(root)
            missing = self.run_wrapper(case, env, table)
            self.assertEqual(missing.returncode, 2, missing.stdout)
            self.assertIn("alpha=0 requires an absolute CL", missing.stdout)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root, alpha=0.0)
            env["FAKE_CL"] = "0.000001"
            env["FAKE_CL_SWING"] = "0.00008"
            result = self.run_wrapper(
                case,
                env,
                self.expected_table(root),
                "--cl-absolute-tolerance",
                "0.0001",
            )
            self.assertEqual(result.returncode, 0, result.stdout)

    def test_populated_cl_cd_ranges_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root)
            row = {
                "case": "synthetic_case", "reference_status": "VERIFIED",
                "mesh": "synthetic", "cl_min": "0.09", "cl_max": "0.11",
                "cd_min": "0.02", "cd_max": "0.025", "residual_target": "-10",
                "load_window": "200", "load_ptp_limit_pct": "1.0",
                "max_nonphysical_points": "0", "notes": "test",
            }
            result = self.run_wrapper(case, env, self.expected_table(root, row))
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("CD final-window mean", result.stdout)
            self.assertIn("above 0.025", result.stdout)

    def test_populated_postprocessing_limits_require_and_enforce_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root)
            row = {
                "case": "synthetic_case", "reference_status": "VERIFIED",
                "mesh": "synthetic", "residual_target": "-10", "load_window": "200",
                "load_ptp_limit_pct": "1.0", "max_nonphysical_points": "0",
                "yplus_target": "1.0", "metrics_file": "case_metrics.json",
                "notes": "test",
            }
            table = self.expected_table(root, row)
            missing = self.run_wrapper(case, env, table)
            self.assertEqual(missing.returncode, 2, missing.stdout)
            self.assertIn("require metrics file", missing.stdout)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root)
            row = {
                "case": "synthetic_case", "reference_status": "VERIFIED",
                "mesh": "synthetic", "residual_target": "-10", "load_window": "200",
                "load_ptp_limit_pct": "1.0", "max_nonphysical_points": "0",
                "yplus_target": "1.0", "metrics_file": "case_metrics.json",
                "notes": "test",
            }
            table = self.expected_table(root, row)
            (case / "case_metrics.json").write_text(
                json.dumps(
                    {
                        "symmetry_error_rms": 0.001,
                        "shock_angle_error_deg": 0.2,
                        "yplus_max": 1.2,
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_wrapper(case, env, table)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("yplus_max 1.2 exceeds", result.stdout)

    def test_stale_output_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, env = self.make_case(root)
            (case / "history_startup.csv").write_text("old\n", encoding="utf-8")
            result = self.run_wrapper(case, env, self.expected_table(root))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing to mix old and new outputs", result.stdout)


if __name__ == "__main__":
    unittest.main()
