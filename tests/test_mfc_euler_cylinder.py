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
        case, metadata = case_module.build_case(
            2.7, "f180", 8.0, 0.1, reynolds=1.0e4
        )
        self.assertEqual(case["model_eqns"], 2)
        self.assertEqual(case["viscous"], "T")
        self.assertEqual(case["weno_Re_flux"], "T")
        self.assertEqual(case["patch_ib(1)%slip"], "F")
        self.assertAlmostEqual(case["fluid_pp(1)%Re(1)"], 1.0e4 / 2.7)
        self.assertEqual(metadata["reynolds_number"], 1.0e4)
        self.assertIn("expert review required", metadata["label_scope"])

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
