#!/usr/bin/env python3
"""Tests for the lossless alpha=0/4/8 dataset packager."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGER = ROOT / "scripts" / "package_euler_a048_full.py"


class PackageEulerA048Tests(unittest.TestCase):
    def make_capture(self, root: Path, omit: str | None = None) -> tuple[Path, Path]:
        run_root = root / "run"
        archive_dir = root / "archives"
        mesh = run_root / "meshes" / "diamond_euler_sharp_medium_720x181.su2"
        mesh.parent.mkdir(parents=True)
        mesh.write_text(
            "NDIME= 2\nNPOIN= 2\n0.0 0.0 0\n1.0 0.0 1\nNELEM= 0\nNMARK= 0\n",
            encoding="ascii",
        )
        (run_root / "status").mkdir()
        for angle in (0, 4, 8):
            name = f"euler_alpha{angle}"
            case = run_root / "cases" / name
            (case / "logs").mkdir(parents=True)
            for cfg in ("startup.cfg", "second_order.cfg"):
                (case / cfg).write_text(f"AOA= {angle}.0\n", encoding="ascii")
            for stage in ("startup", "second_order"):
                history = case / f"history_{stage}.csv"
                with history.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["Inner_Iter", "rms[Rho]", "CL", "CD"])
                    writer.writerow([0, -10.5, 0.1, 0.03])
                restart = case / f"restart_{stage}.csv"
                with restart.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(
                        ["PointID", "x", "y", "Density", "Momentum_x", "Momentum_y", "Energy"]
                    )
                    writer.writerow([0, 0.0, 0.0, 1.0, 3.0, 0.0, 7.0])
                    writer.writerow([1, 1.0, 0.0, 1.1, 3.1, 0.1, 7.2])
                (case / f"flow_{stage}.vtu").write_text(
                    '<VTKFile type="UnstructuredGrid"><UnstructuredGrid/></VTKFile>\n',
                    encoding="ascii",
                )
                (case / f"surface_flow_{stage}.csv").write_text(
                    "x,y,Pressure\n0,0,1\n", encoding="ascii"
                )
            (case / "logs" / "20260903T000000Z_run_manifest.json").write_text(
                json.dumps({"result": "PASS"}), encoding="utf-8"
            )
            (run_root / "status" / f"{name}.rc").write_text("0\n", encoding="ascii")
        if omit:
            (run_root / omit).unlink()
        return run_root, archive_dir

    def run_packager(self, run_root: Path, archive_dir: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(PACKAGER),
                "--run-root",
                str(run_root),
                "--archive-dir",
                str(archive_dir),
                "--job-id",
                "123",
                "--timestamp",
                "20260903T000000Z",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def test_complete_capture_builds_crc_valid_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            run_root, archive_dir = self.make_capture(Path(directory))
            result = self.run_packager(run_root, archive_dir)
            self.assertEqual(result.returncode, 0, result.stdout)
            archives = list(archive_dir.glob("*.zip"))
            self.assertEqual(len(archives), 1)
            with zipfile.ZipFile(archives[0]) as package:
                self.assertIsNone(package.testzip())
                names = set(package.namelist())
            self.assertIn(
                "SU2_EULER_M3_AOA_0_4_8_FULL/DATASET_MANIFEST.json", names
            )
            manifest = json.loads((run_root / "DATASET_MANIFEST.json").read_text())
            self.assertTrue(manifest["capture_complete"])
            self.assertTrue(manifest["science_qa_passed"])
            self.assertEqual(manifest["mesh_family"], "common sharp Euler O-grid 720x181")
            self.assertIn("not the unavailable refined-triangular", manifest["scope"])

    def test_missing_raw_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            run_root, archive_dir = self.make_capture(
                Path(directory), "cases/euler_alpha8/flow_second_order.vtu"
            )
            result = self.run_packager(run_root, archive_dir)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("flow_second_order.vtu", result.stdout)
            self.assertTrue((run_root / "DATASET_FAILED.json").is_file())
            self.assertFalse(list(archive_dir.glob("*.zip")))


if __name__ == "__main__":
    unittest.main()
