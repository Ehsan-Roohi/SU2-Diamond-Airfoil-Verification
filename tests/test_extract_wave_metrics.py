#!/usr/bin/env python3
"""Synthetic tests for native-grid density-gradient metric extraction."""

from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract_wave_metrics.py"


class ExtractWaveMetricsTests(unittest.TestCase):
    def write_grid(self, root: Path, density_function) -> tuple[Path, Path]:
        nx, ny = 41, 81
        coordinates = []
        for j in range(ny):
            y = -0.5 + j / (ny - 1)
            for i in range(nx):
                x = 0.5 * i / (nx - 1)
                coordinates.append((x, y))

        restart = root / "restart_second_order.csv"
        with restart.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("PointID", "x", "y", "Density"))
            for point_id, (x, y) in enumerate(coordinates):
                writer.writerow((point_id, x, y, density_function(x, y)))

        mesh = root / "mesh.su2"
        lines = ["NDIME= 2", f"NPOIN= {len(coordinates)}"]
        lines.extend(f"{x:.16g} {y:.16g} {point_id}" for point_id, (x, y) in enumerate(coordinates))
        elements = []
        for j in range(ny - 1):
            for i in range(nx - 1):
                n0 = j * nx + i
                n1 = n0 + 1
                n3 = (j + 1) * nx + i
                n2 = n3 + 1
                elements.append(f"9 {n0} {n1} {n2} {n3} {len(elements)}")
        lines.append(f"NELEM= {len(elements)}")
        lines.extend(elements)
        mesh.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return restart, mesh

    def test_recovers_synthetic_upper_shock_angle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            slope = math.tan(math.radians(25.0))
            restart, mesh = self.write_grid(
                root,
                lambda x, y: 1.0 + 0.2 * (1.0 + math.tanh((y - slope * x) / 0.012)),
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(restart),
                    "--mesh",
                    str(mesh),
                    "--branch",
                    "upper",
                    "--x-max",
                    "0.2",
                    "--reference-angle-deg",
                    "25.0",
                    "--metrics-json",
                    str(root / "metrics.json"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
            self.assertLess(metrics["shock_angle_error_deg"], 1.5)
            self.assertTrue((root / "shock_ridge_upper.csv").is_file())

    def test_symmetry_and_yplus_are_written_with_explicit_definitions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            restart, mesh = self.write_grid(
                root, lambda x, y: 1.0 + 0.1 * x + 0.02 * y * y
            )
            surface = root / "surface.csv"
            surface.write_text(
                '"PointID","Y_Plus"\n0,0.1\n1,-0.4\n2,0.2\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(restart),
                    "--mesh",
                    str(mesh),
                    "--skip-shock",
                    "--symmetry",
                    "--surface-csv",
                    str(surface),
                    "--metrics-json",
                    str(root / "metrics.json"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
            self.assertLess(metrics["symmetry_error_rms"], 1.0e-12)
            self.assertEqual(metrics["yplus_max"], 0.4)
            self.assertEqual(metrics["yplus_samples"], 3)


if __name__ == "__main__":
    unittest.main()
