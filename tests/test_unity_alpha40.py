#!/usr/bin/env python3
"""Static tests for the fail-closed Unity alpha40 runner."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "unity_alpha40", ROOT / "scripts" / "unity_alpha40.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


BASE_CFG = """\
SOLVER= RANS
KIND_TURB_MODEL= SST
MACH_NUMBER= 3.0
AOA= 40.0
REYNOLDS_NUMBER= 1.0E6
MESH_FILENAME= ../../meshes/diamond_medium_720x181.su2
CONV_NUM_METHOD_FLOW= ROE
RESTART_SOL= YES
ITER= 20000
"""


class UnityAlpha40Tests(unittest.TestCase):
    def make_seed(self, root: Path, cfg: str = BASE_CFG) -> Path:
        path = root / MODULE.EXPECTED_SEED_NAME
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("seed/alpha40.cfg", cfg)
            archive.writestr(
                "seed/restart_iter20000.csv",
                '"PointID","Density"\n0,1.0\n',
            )
        return path

    def test_seed_requires_exact_scientific_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            info = MODULE.inspect_seed(self.make_seed(root))
            self.assertEqual(info["config_member"], "seed/alpha40.cfg")
            self.assertEqual(info["restart_member"], "seed/restart_iter20000.csv")
            bad = BASE_CFG.replace("AOA= 40.0", "AOA= 39.0")
            with self.assertRaises(MODULE.GateFailure):
                MODULE.inspect_seed(self.make_seed(root, bad))

    def test_generated_configs_lock_validated_time_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "meshes").mkdir()
            bdf1, _ = MODULE.make_config(BASE_CFG, root, 1, 1)
            first = MODULE.parse_cfg_text(bdf1)
            self.assertEqual(first["TIME_MARCHING"], "DUAL_TIME_STEPPING-1ST_ORDER")
            self.assertEqual(first["INNER_ITER"], "2000")
            self.assertEqual(first["TIME_STEP"], "2.5e-06")
            self.assertEqual(first["RESTART_ITER"], "1")
            self.assertEqual(first["TIME_ITER"], "2")

            bdf2, _ = MODULE.make_config(BASE_CFG, root, 2, 4)
            second = MODULE.parse_cfg_text(bdf2)
            self.assertEqual(second["TIME_MARCHING"], "DUAL_TIME_STEPPING-2ND_ORDER")
            self.assertEqual(second["INNER_ITER"], "600")
            self.assertEqual(second["RESTART_ITER"], "2")
            self.assertEqual(second["TIME_ITER"], "5")
            self.assertEqual(second["CONV_NUM_METHOD_FLOW"], "ROE")

            binary, _ = MODULE.make_config(
                BASE_CFG, root, 2, 4, restart_extension=".dat"
            )
            binary_values = MODULE.parse_cfg_text(binary)
            self.assertEqual(binary_values["READ_BINARY_RESTART"], "YES")
            self.assertEqual(binary_values["OUTPUT_FILES"], "(RESTART)")

    def test_bdf2_requires_two_restart_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            MODULE.restart_path(root, 0).write_text("zero", encoding="utf-8")
            with self.assertRaises(MODULE.GateFailure):
                MODULE.require_restart_levels(root, 2)
            MODULE.restart_path(root, 1).write_text("one", encoding="utf-8")
            MODULE.require_restart_levels(root, 2)

    def test_cfg_override_does_not_change_spatial_scheme(self):
        rendered = MODULE.cfg_with_overrides(
            BASE_CFG, {"TIME_DOMAIN": "YES", "INNER_ITER": "600"}
        )
        values = MODULE.parse_cfg_text(rendered)
        self.assertEqual(values["CONV_NUM_METHOD_FLOW"], "ROE")
        self.assertEqual(values["TIME_DOMAIN"], "YES")
        self.assertEqual(values["INNER_ITER"], "600")


if __name__ == "__main__":
    unittest.main()
