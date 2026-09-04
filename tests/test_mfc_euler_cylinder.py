#!/usr/bin/env python3
"""Static tests for the MFC Euler circular-cylinder validation case."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = ROOT / "mfc_euler_cylinder" / "case.py"
RH_PATH = ROOT / "mfc_euler_cylinder" / "rankine_hugoniot_reference.py"
VCFL_RECOVERY_PATH = (
    ROOT / "mfc_euler_cylinder" / "unity_recover_viscous_cylinder_vcfl.sh"
)
VORTEX_CONTROLS_PATH = (
    ROOT / "mfc_euler_cylinder" / "unity_submit_euler_vortex_controls.sh"
)
VORTEX_PROTOCOL_PATH = (
    ROOT / "mfc_euler_cylinder" / "vortex_sensitivity_protocol.json"
)
PACKAGE_COMPLETED_PATH = (
    ROOT / "mfc_euler_cylinder" / "unity_package_completed_cylinder_runs.sh"
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


case_module = load_module(CASE_PATH, "mfc_euler_cylinder_case")
rh_module = load_module(RH_PATH, "mfc_euler_cylinder_rh")


class MFCEulerCylinderTests(unittest.TestCase):
    def test_case_is_inviscid_slip_circle_without_reynolds_parameter(self):
        case, metadata = case_module.build_case(2.7, "f90", 3.0, 0.1)
        self.assertEqual(case["model_eqns"], 2)
        self.assertEqual(case["viscous"], "F")
        self.assertEqual(case["patch_ib(1)%geometry"], 2)
        self.assertEqual(case["patch_ib(1)%radius"], 0.5)
        self.assertEqual(case["patch_ib(1)%slip"], "T")
        self.assertFalse(any(key.startswith("fluid_pp(1)%Re") for key in case))
        self.assertIn("vortex is not ground truth", metadata["label_scope"])

    def test_viscous_mode_is_no_slip_and_uses_requested_reynolds_number(self):
        euler, _ = case_module.build_case(2.7, "f180", 8.0, 0.1)
        case, metadata = case_module.build_case(
            2.7, "f180", 8.0, 0.1, reynolds=1.0e4
        )
        self.assertEqual(case["model_eqns"], 2)
        self.assertEqual(case["viscous"], "T")
        self.assertEqual(case["weno_Re_flux"], "T")
        self.assertEqual(case["patch_ib(1)%slip"], "F")
        self.assertAlmostEqual(case["fluid_pp(1)%Re(1)"], 1.0e4 / 2.7)
        self.assertAlmostEqual(case["dt"], euler["dt"] / 4.0)
        self.assertEqual(metadata["cfl_coefficient"], 0.05)
        self.assertEqual(metadata["reynolds_number"], 1.0e4)
        self.assertIn("expert review required", metadata["label_scope"])

    def test_explicit_cfl_override_halves_euler_time_step_only(self):
        baseline, baseline_metadata = case_module.build_case(
            2.7, "f90", 8.0, 0.1
        )
        control, control_metadata = case_module.build_case(
            2.7, "f90", 8.0, 0.1, cfl_coefficient=0.10
        )
        self.assertAlmostEqual(control["dt"], baseline["dt"] / 2.0)
        self.assertEqual(control["m"], baseline["m"])
        self.assertEqual(control["n"], baseline["n"])
        self.assertEqual(control["viscous"], "F")
        self.assertEqual(baseline_metadata["cfl_coefficient"], 0.20)
        self.assertFalse(baseline_metadata["cfl_was_overridden"])
        self.assertEqual(control_metadata["cfl_coefficient"], 0.10)
        self.assertEqual(control_metadata["default_cfl_coefficient"], 0.20)
        self.assertTrue(control_metadata["cfl_was_overridden"])

    def test_cfl_override_rejects_nonphysical_values(self):
        for value in (0.0, -0.1, 0.6, float("inf"), float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    case_module.build_case(
                        2.7, "f90", 8.0, 0.1, cfl_coefficient=value
                    )

    def test_viscous_restart_reindexes_physical_time_on_safe_clock(self):
        case, metadata = case_module.build_case(
            2.7,
            "f180",
            0.7,
            0.025,
            reynolds=1.0e4,
            start_time=0.5,
            restart=True,
        )
        self.assertEqual(case["t_step_start"], 6660)
        self.assertEqual(case["t_step_stop"], 9324)
        self.assertEqual(case["t_step_save"], 333)
        self.assertEqual(case["num_patches"], 0)
        self.assertEqual(case["old_ic"], "T")
        self.assertEqual(case["old_grid"], "T")
        self.assertEqual(case["t_step_old"], 0)
        self.assertNotIn("patch_icpp(1)%geometry", case)
        self.assertAlmostEqual(metadata["actual_start_time"], 0.5)

    def test_restart_arguments_are_consistent(self):
        with self.assertRaises(ValueError):
            case_module.build_case(
                2.7, "f180", 0.7, 0.025, reynolds=1.0e4,
                start_time=0.5, restart=False
            )
        with self.assertRaises(ValueError):
            case_module.build_case(
                2.7, "f180", 0.7, 0.025, reynolds=1.0e4,
                start_time=0.0, restart=True
            )

    def test_reynolds_validation_rejects_ambiguous_small_values(self):
        with self.assertRaises(ValueError):
            case_module.build_case(2.7, "f90", 3.0, 0.1, reynolds=-1.0)
        with self.assertRaises(ValueError):
            case_module.build_case(2.7, "f90", 3.0, 0.1, reynolds=10.0)

    def test_scientific_grids_have_declared_cells_per_diameter(self):
        for name in ("f90", "f180", "f270"):
            case, metadata = case_module.build_case(3.0, name, 3.0, 0.1)
            dx = (case["x_domain%end"] - case["x_domain%beg"]) / (case["m"] + 1)
            self.assertAlmostEqual(1.0 / dx, metadata["cells_per_diameter"], places=12)
            self.assertEqual(case["t_step_stop"] % case["t_step_save"], 0)
            self.assertEqual(
                case["t_step_stop"] // case["t_step_save"] + 1,
                metadata["saved_states_including_initial"],
            )

    def test_command_line_emits_only_an_mfc_json_dictionary(self):
        raw = subprocess.check_output(
            [sys.executable, str(CASE_PATH), "--mach", "3", "--grid", "smoke",
             "--final-time", "0.05", "--save-dt", "0.025"],
            text=True,
        )
        case = json.loads(raw)
        self.assertEqual(case["patch_icpp(1)%vel(1)"], 3.0)
        self.assertEqual(case["bc_x%beg"], -11)
        self.assertEqual(case["bc_x%end"], -12)

        raw_viscous = subprocess.check_output(
            [
                sys.executable,
                str(CASE_PATH),
                "--mach",
                "2.7",
                "--grid",
                "f180",
                "--final-time",
                "8",
                "--save-dt",
                "0.1",
                "--reynolds",
                "50000",
            ],
            text=True,
        )
        viscous = json.loads(raw_viscous)
        self.assertEqual(viscous["viscous"], "T")
        self.assertEqual(viscous["patch_ib(1)%slip"], "F")

        raw_cfl = subprocess.check_output(
            [
                sys.executable,
                str(CASE_PATH),
                "--mach",
                "2.7",
                "--grid",
                "f90",
                "--final-time",
                "8",
                "--save-dt",
                "0.1",
                "--cfl-coefficient",
                "0.1",
            ],
            text=True,
        )
        cfl_control = json.loads(raw_cfl)
        self.assertAlmostEqual(cfl_control["dt"], case_module.CFL_COEFFICIENT / 2 * (1 / 90) / 3.7)

    def test_binary_postprocess_mode_changes_only_the_output_format(self):
        silo, _ = case_module.build_case(2.7, "f90", 3.0, 0.1, "silo")
        binary, metadata = case_module.build_case(
            2.7, "f90", 3.0, 0.1, "binary"
        )
        self.assertEqual(silo["format"], 1)
        self.assertEqual(binary["format"], 2)
        self.assertEqual(metadata["output_format"], "binary")
        for key in silo:
            if key != "format":
                self.assertEqual(silo[key], binary[key])

    def test_unity_launcher_has_read_only_postprocess_recovery(self):
        launcher = (
            ROOT
            / "mfc_euler_cylinder"
            / "unity_submit_euler_cylinder.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('RECOVER_CASE_DIR="${RECOVER_CASE_DIR:-}"', launcher)
        self.assertIn("/scratch4/workspace/", launcher)
        self.assertIn('ln -s "$source" "$target"', launcher)
        self.assertIn('-n 1 -j 1', launcher)
        self.assertIn('--format binary', launcher)
        self.assertNotIn("-t pre_process simulation post_process", launcher)

    def test_unity_launcher_supports_explicit_viscous_mode(self):
        launcher = (
            ROOT
            / "mfc_euler_cylinder"
            / "unity_submit_euler_cylinder.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('REYNOLDS="${REYNOLDS:-0}"', launcher)
        self.assertIn('CASE_ARGS+=(--reynolds "$REYNOLDS")', launcher)
        self.assertIn("mfc_viscous_cylinder", launcher)
        self.assertIn("RUN_OK_MFC_CYLINDER.txt", launcher)
        self.assertNotIn("EXPECTED_SNAPSHOTS=31", launcher)

    def test_unity_launcher_forwards_and_records_explicit_cfl(self):
        launcher = (
            ROOT
            / "mfc_euler_cylinder"
            / "unity_submit_euler_cylinder.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('CFL_COEFFICIENT="${CFL_COEFFICIENT:-auto}"', launcher)
        self.assertIn('CASE_ARGS+=(--cfl-coefficient "$CFL_COEFFICIENT")', launcher)
        self.assertIn('printf \'CFL_COEFFICIENT=%q\\n\'', launcher)
        self.assertIn("cfl_coefficient=%s", launcher)

    def test_vortex_control_wrapper_submits_exact_two_factor_matrix(self):
        launcher = VORTEX_CONTROLS_PATH.read_text(encoding="utf-8")
        self.assertIn("submit_control grid_f180_cfl0p20 f180 0.20", launcher)
        self.assertIn("submit_control timestep_f90_cfl0p10 f90 0.10", launcher)
        self.assertIn('FINAL_TIME="${FINAL_TIME:-8}"', launcher)
        self.assertIn('SAVE_DT="${SAVE_DT:-0.1}"', launcher)
        self.assertIn('local build_root="$MFC_CYL_PARENT/$label"', launcher)
        self.assertNotIn("rm -rf", launcher)

    def test_vortex_sensitivity_protocol_keeps_baseline_and_controls_distinct(self):
        protocol = json.loads(VORTEX_PROTOCOL_PATH.read_text(encoding="utf-8"))
        matrix = protocol["matrix"]
        self.assertEqual(len(matrix), 3)
        self.assertEqual(
            [(item["grid"], item["cfl_coefficient"]) for item in matrix],
            [("f90", 0.2), ("f180", 0.2), ("f90", 0.1)],
        )
        self.assertFalse(matrix[0]["submit_by_control_wrapper"])
        self.assertTrue(matrix[1]["submit_by_control_wrapper"])
        self.assertTrue(matrix[2]["submit_by_control_wrapper"])
        self.assertIn("Do not claim", protocol["decision_rule"])
        for item in matrix[1:]:
            case, _ = case_module.build_case(
                2.7,
                item["grid"],
                8.0,
                0.1,
                cfl_coefficient=item["cfl_coefficient"],
            )
            self.assertAlmostEqual(case["dt"], item["solver_dt"])
            self.assertEqual(
                case["t_step_stop"] // case["t_step_save"] + 1,
                item["expected_snapshots"],
            )

    def test_completed_run_packager_requires_pass_and_preserves_raw_restarts(self):
        packager = PACKAGE_COMPLETED_PATH.read_text(encoding="utf-8")
        self.assertIn("latest_pass_marker", packager)
        self.assertIn("grid_f180_cfl0p20", packager)
        self.assertIn("timestep_f90_cfl0p10", packager)
        self.assertIn("RUN_OK_MFC_VISCOUS_CYLINDER_RECOVERED.txt", packager)
        self.assertIn("--exclude='*/restart_data'", packager)
        self.assertIn("RUN_OK_CYLINDER_PACKAGES.txt", packager)
        self.assertIn("sha256sum", packager)
        self.assertIn("sbatch --parsable", packager)
        self.assertIn('INCLUDE_VISCOUS="${INCLUDE_VISCOUS:-1}"', packager)
        self.assertIn('if [[ "$INCLUDE_VISCOUS" == 1 ]]', packager)
        self.assertIn("archive_count=3", packager)
        self.assertNotIn("rm -rf", packager)

    def test_vcfl_recovery_is_dt4_restart_and_afterok_chained(self):
        launcher = VCFL_RECOVERY_PATH.read_text(encoding="utf-8")
        self.assertIn('SOURCE_SAFE_STEP="${SOURCE_SAFE_STEP:-auto}"', launcher)
        self.assertIn("source_dt / new_dt, 4.0", launcher)
        self.assertIn('GATE_FINAL_TIME="${GATE_FINAL_TIME:-0.7}"', launcher)
        self.assertIn('--dependency="afterok:$GATE_JOB"', launcher)
        self.assertIn('--dependency="afterok:$PROD1_JOB"', launcher)
        self.assertIn('--dependency="afterok:$PROD2_JOB"', launcher)
        self.assertIn("RUN_OK_MFC_VISCOUS_CYLINDER_RECOVERED.txt", launcher)
        self.assertIn('cp --reflink=auto "$SOURCE_STATE"', launcher)
        self.assertNotIn('rm -rf', launcher)

    def test_mach3_normal_shock_reference(self):
        result = rh_module.normal_shock(3.0)
        self.assertAlmostEqual(result["rho2_over_rho1"], 27.0 / 7.0, places=12)
        self.assertAlmostEqual(result["p2_over_p1"], 31.0 / 3.0, places=12)
        self.assertLess(result["mach_downstream"], 1.0)
        self.assertGreater(result["inviscid_stagnation_cp"], 1.0)

    def test_validation_protocol_forbids_finite_reynolds_claim(self):
        protocol = json.loads(
            (ROOT / "mfc_euler_cylinder" / "validation_protocol.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIsNone(protocol["physics"]["reynolds_number"])
        self.assertIn("Do not report", protocol["physics"]["reynolds_note"])
        self.assertEqual(protocol["run_order"][0]["mach"], 2.7)
        self.assertEqual(protocol["run_order"][0]["grid"], "f90")


if __name__ == "__main__":
    unittest.main()
