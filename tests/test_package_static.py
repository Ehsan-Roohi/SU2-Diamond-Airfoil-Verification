#!/usr/bin/env python3
"""Static package-integrity tests that do not run SU2."""

from __future__ import annotations

import csv
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "cases"


def cfg_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("%", 1)[0].strip()
        if line and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip().upper()] = value.strip()
    return values


class PackageStaticTests(unittest.TestCase):
    def configs(self) -> list[Path]:
        return sorted(CASES.glob("*/*.cfg"))

    def test_twelve_cases_and_twenty_four_configs_are_present(self):
        self.assertEqual(len([path for path in CASES.iterdir() if path.is_dir()]), 12)
        self.assertEqual(len(self.configs()), 24)

    def test_history_and_ascii_output_names_are_current(self):
        for path in self.configs():
            text = path.read_text(encoding="utf-8")
            values = cfg_values(path)
            self.assertNotIn("SURFACE_AERO_COEFF", text, path)
            self.assertIn("AERO_COEFF_SURF", values.get("HISTORY_OUTPUT", ""), path)
            self.assertIn("RESTART_ASCII", values.get("OUTPUT_FILES", ""), path)
            self.assertFalse(values["RESTART_FILENAME"].endswith(".dat"), path)

    def test_sst_transport_and_freestream_inputs_are_explicit(self):
        for path in sorted(CASES.glob("sst_*/*.cfg")):
            values = cfg_values(path)
            self.assertEqual(values.get("MUSCL_TURB"), "NO", path)
            self.assertEqual(values.get("FREESTREAM_TURBULENCEINTENSITY"), "0.05", path)
            self.assertEqual(values.get("FREESTREAM_TURB2LAMVISCRATIO"), "10.0", path)

    def test_euler_configs_use_audited_sharp_mesh_and_conservative_hllc(self):
        for path in sorted(CASES.glob("euler_*/*.cfg")):
            values = cfg_values(path)
            self.assertEqual(values.get("CONV_NUM_METHOD_FLOW"), "HLLC", path)
            expected_cfl = "0.1" if path.name == "startup.cfg" else "0.2"
            self.assertEqual(values.get("CFL_NUMBER"), expected_cfl, path)
            expected_iterations = "600" if path.name == "startup.cfg" else "2000"
            self.assertEqual(values.get("ITER"), expected_iterations, path)
            self.assertIn("diamond_euler_sharp_medium_720x181.su2", values["MESH_FILENAME"])

    def test_mesh_references_resolve_and_expected_table_covers_cases(self):
        case_names = {path.name for path in CASES.iterdir() if path.is_dir()}
        for path in self.configs():
            mesh = (path.parent / cfg_values(path)["MESH_FILENAME"]).resolve()
            self.assertTrue(mesh.is_file(), f"{path}: missing {mesh}")
        with (ROOT / "EXPECTED_RESULTS.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual({row["case"] for row in rows}, case_names)
        alpha0 = next(row for row in rows if row["case"] == "euler_alpha0")
        self.assertEqual(
            alpha0["reference_status"],
            "QUALIFIED_TEACHING_REFERENCE_NOT_GRID_BENCHMARK",
        )
        self.assertEqual(alpha0["residual_policy"], "warning")
        self.assertEqual(alpha0["max_nonphysical_points"], "0")
        self.assertEqual(alpha0["mesh"], "diamond_euler_sharp_medium_720x181.su2")
        archived_report_cases = {
            "euler_alpha0", "euler_alpha1", "euler_alpha2",
            "euler_alpha3", "euler_alpha4",
        }
        for row in rows:
            self.assertNotEqual(row["reference_status"], "VERIFIED")
            if row["case"] in archived_report_cases:
                for key in ("cl_min", "cl_max", "cd_min", "cd_max"):
                    self.assertNotEqual(
                        row[key], "", f"missing archived range in {row['case']}:{key}"
                    )
            else:
                for key in ("cl_min", "cl_max", "cd_min", "cd_max"):
                    self.assertEqual(row[key], "", f"unarchived range in {row['case']}:{key}")


if __name__ == "__main__":
    unittest.main()
