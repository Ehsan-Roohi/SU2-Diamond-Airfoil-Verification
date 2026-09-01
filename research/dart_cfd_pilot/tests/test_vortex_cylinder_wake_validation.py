import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_vortex_cylinder_wake_validation.py"
SRA_RUNNER = ROOT / "scripts" / "run_vortex_shock_ridge_aware_su2.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_lbm_solver_compiles_and_writes_smoke_sequence(tmp_path):
    source = ROOT / "scripts" / "cylinder_lbm_d2q9.c"
    binary = tmp_path / "cylinder_lbm_d2q9"
    subprocess.run(
        [
            "cc", "-O2", "-fopenmp", "-std=c11", "-Wall", "-Wextra",
            "-Werror", str(source), "-lm", "-o", str(binary),
        ],
        check=True,
    )
    output = tmp_path / "smoke"
    subprocess.run(
        [str(binary), "64", "48", "8", "100", "0.08", "2", "0", "1", "1", str(output)],
        check=True,
        timeout=30,
    )
    assert len(list(output.glob("snapshot_*.bin"))) == 3
    assert (output / "cylinder_monitor.csv").is_file()


def test_gamma2_reference_finds_a_lamb_oseen_core():
    runner = load_module("test_cylinder_runner", RUNNER)
    x = np.linspace(-1.0, 1.0, 101)
    xx, yy = np.meshgrid(x, x, indexing="ij")
    radius_squared = xx * xx + yy * yy
    circulation = 1.0
    core_radius = 0.15
    factor = circulation * (1.0 - np.exp(-radius_squared / core_radius**2))
    factor /= 2.0 * math.pi * np.maximum(radius_squared, 1.0e-20)
    u = -factor * yy
    v = factor * xx
    fluid = np.ones_like(u, dtype=bool)
    gamma2 = runner.gamma2_field(u, v, fluid, radius=4)
    peak = np.unravel_index(np.nanargmax(gamma2), gamma2.shape)
    assert abs(x[peak[0]]) <= 0.04
    assert abs(x[peak[1]]) <= 0.04
    assert gamma2[peak] >= 2.0 / math.pi


def test_scale_adaptive_pressure_law_preserves_frozen_default():
    sra = load_module("test_cylinder_sra", SRA_RUNNER)
    pressure = {"ring_support": 2, "offset_cells": 3.0, "pass": False}
    island = {"area_cells": 144.0}
    frozen = {
        "maximum_pressure_minimum_offset_cells": 2.0,
        "minimum_pressure_ring_support": 2,
    }
    unchanged = sra.scale_adaptive_pressure_support(pressure, island, frozen)
    assert unchanged["allowed_offset_cells"] == 2.0
    assert unchanged["pass"] is False

    adaptive = dict(frozen)
    adaptive.update({
        "pressure_offset_equivalent_radius_fraction": 0.5,
        "maximum_scale_adaptive_pressure_offset_cells": 5.0,
    })
    accepted = sra.scale_adaptive_pressure_support(pressure, island, adaptive)
    assert accepted["allowed_offset_cells"] > 2.0
    assert accepted["pass"] is True


def test_development_and_holdout_protocols_are_distinct_and_frozen():
    development = json.loads((ROOT / "vortex_cylinder_wake_validation.json").read_text())
    holdout = json.loads((ROOT / "vortex_cylinder_wake_re150_holdout.json").read_text())
    adaptive = json.loads((ROOT / "vortex_scale_adaptive_sra_cmcd.json").read_text())
    assert development["solver"]["reynolds_number"] == 100.0
    assert holdout["solver"]["reynolds_number"] == 150.0
    assert development["case_id"] != holdout["case_id"]
    assert holdout["frozen_detector_sources"]["detector_recalibration_allowed"] is False
    assert adaptive["future_case_recalibration_allowed"] is False
    assert "independent" in holdout["case_role"]
    assert "holdout" in holdout["case_role"]


def test_unity_archive_is_flat_and_not_named_stage():
    submit = ROOT / "scripts" / "submit_unity_vortex_cylinder_wake_validation.sh"
    text = submit.read_text()
    assert "${PROJECT_ROOT}/VORTEX_CYLINDER_WAKE_" in text
    assert "PYTHONNOUSERSITE=1" in text
    assert "stage" not in submit.name
