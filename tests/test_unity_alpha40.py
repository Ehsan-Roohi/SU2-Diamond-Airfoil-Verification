#!/usr/bin/env python3
"""Static tests for the fail-closed Unity alpha40 runner."""

from __future__ import annotations

import importlib.util
import hashlib
import json
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

    def make_resume(self, root: Path, seed: Path) -> Path:
        seed_info = MODULE.inspect_seed(seed)
        status = {
            "case": MODULE.CASE_NAME,
            "dt_seconds": MODULE.PHYSICAL_DT,
            "message": "chunk completed",
            "metrics": None,
            "nonphysical_points": 0,
            "qualification": "NOT_QUALIFIED",
            "status": "CHECKPOINTED",
            "target_time_step": MODULE.DEFAULT_TARGET_STEP,
            "time_step": 664,
        }
        seed_manifest = {
            **seed_info,
            "case": MODULE.CASE_NAME,
            "restart_extension": ".csv",
        }
        payloads = {
            "restart_medium_halfdt_00663.csv": b'"PointID","Density"\n0,1.0\n',
            "restart_medium_halfdt_00664.csv": b'"PointID","Density"\n0,1.0\n',
            "seed/seed_original.cfg": BASE_CFG.encode(),
            "seed/seed_manifest.json": json.dumps(seed_manifest).encode(),
            "status.json": json.dumps(status).encode(),
        }
        manifest = {
            **status,
            "checkpoint_file": (
                "URANS_alpha40_medium_halfdt_checkpoint_t000664.zip"
            ),
            "included_sha256": {
                name: hashlib.sha256(data).hexdigest()
                for name, data in payloads.items()
            },
        }
        path = root / manifest["checkpoint_file"]
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in payloads.items():
                archive.writestr(name, data)
            archive.writestr("checkpoint_manifest.json", json.dumps(manifest))
        return path

    def make_resume_parts(self, root: Path, resume: Path) -> Path:
        parts_dir = root / MODULE.RESUME_PARTS_DIRNAME
        parts_dir.mkdir()
        archive = resume.read_bytes()
        parts = []
        for index, offset in enumerate(range(0, len(archive), 97)):
            data = archive[offset : offset + 97]
            name = f"part-{index:04d}"
            (parts_dir / name).write_bytes(data)
            parts.append(
                {
                    "name": name,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        (parts_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "archive_name": MODULE.RESUME_ARCHIVE_NAME,
                    "archive_size_bytes": len(archive),
                    "archive_sha256": hashlib.sha256(archive).hexdigest(),
                    "parts": parts,
                }
            ),
            encoding="utf-8",
        )
        return parts_dir

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
            self.assertNotIn("ITER", first)

            bdf2, _ = MODULE.make_config(BASE_CFG, root, 2, 4)
            second = MODULE.parse_cfg_text(bdf2)
            self.assertEqual(second["TIME_MARCHING"], "DUAL_TIME_STEPPING-2ND_ORDER")
            self.assertEqual(second["INNER_ITER"], "600")
            self.assertEqual(second["RESTART_ITER"], "2")
            self.assertEqual(second["TIME_ITER"], "5")
            self.assertEqual(second["CONV_NUM_METHOD_FLOW"], "ROE")
            self.assertNotIn("ITER", second)

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

    def test_solver_receives_absolute_config_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "run"
            config_dir = run_root / "configs"
            log_dir = run_root / "logs"
            config_dir.mkdir(parents=True)
            log_dir.mkdir()
            cfg_path = config_dir / "chunk.cfg"
            cfg_path.write_text("SOLVER= RANS\n", encoding="utf-8")
            solver = root / "mock_solver.sh"
            solver.write_text(
                "#!/usr/bin/env bash\n"
                "last=\"${@: -1}\"\n"
                "[[ \"$last\" = /* && -f \"$last\" ]]\n",
                encoding="utf-8",
            )
            solver.chmod(0o755)
            log_path = log_dir / "solver.log"
            returncode = MODULE.run_solver(
                solver, 2, cfg_path, run_root, log_path
            )
            self.assertEqual(returncode, 0)
            self.assertIn(str(cfg_path.resolve()), log_path.read_text())

    def test_resume_checkpoint_restores_step_664(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = self.make_seed(root)
            resume = self.make_resume(root, seed)
            mesh_dir = root / "meshes"
            mesh_dir.mkdir()
            (mesh_dir / MODULE.MESH_BASENAME).write_text(
                f"NPOIN= {MODULE.EXPECTED_MESH_POINTS}\n", encoding="utf-8"
            )
            run_root = root / "run"
            info = MODULE.restore_checkpoint(resume, seed, root, run_root)
            self.assertEqual(info["time_step"], 664)
            self.assertEqual(MODULE.latest_restart_step(run_root), 664)
            MODULE.require_restart_levels(run_root, 665)
            self.assertTrue(
                (run_root / "seed" / "resume_checkpoint_manifest.json").is_file()
            )

    def test_restart_pruning_retains_only_two_bdf_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            for step in range(660, 665):
                MODULE.restart_path(run_root, step).write_bytes(b"restart")
            removed_files, removed_bytes = MODULE.prune_restart_levels(
                run_root, 664, ".csv"
            )
            self.assertEqual(removed_files, 3)
            self.assertEqual(removed_bytes, 21)
            self.assertEqual(
                sorted(path.name for path in run_root.glob("restart_*.csv")),
                [
                    "restart_medium_halfdt_00663.csv",
                    "restart_medium_halfdt_00664.csv",
                ],
            )

    def test_bundled_resume_parts_reassemble_exact_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resume = self.make_resume(root, self.make_seed(root))
            parts_dir = self.make_resume_parts(root, resume)
            output = root / "assembled" / MODULE.RESUME_ARCHIVE_NAME
            result = MODULE.assemble_resume_parts(parts_dir, output)
            self.assertFalse(result["reused"])
            self.assertEqual(output.read_bytes(), resume.read_bytes())
            reused = MODULE.assemble_resume_parts(parts_dir, output)
            self.assertTrue(reused["reused"])


if __name__ == "__main__":
    unittest.main()
