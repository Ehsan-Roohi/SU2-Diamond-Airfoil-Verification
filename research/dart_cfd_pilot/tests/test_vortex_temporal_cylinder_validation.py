import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "temporal_vortex_recovery.py"


def load_module():
    spec = importlib.util.spec_from_file_location("temporal_vortex_recovery_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def configuration():
    return json.loads((ROOT / "vortex_temporal_sa_sra_cmcd.json").read_text())


def protocol():
    return {
        "solver": {"inlet_lattice_velocity": 0.1, "diameter_cells": 25},
    }


def accepted(x, sign=1):
    return {"x": x, "y": 1.0, "sign": sign}


def provisional(x, sign=1):
    return {
        "x": x,
        "y": 1.0,
        "sign": sign,
        "accepted": False,
        "rejection_reason": "pressure_minimum_not_corroborated",
        "q_island_pass": True,
        "winding_support": 3,
        "pressure_core": {"ring_support": 3},
    }


def record(step, detections=None, audit=None):
    rows = list(detections or [])
    return {
        "step": step,
        "base_detections": rows,
        "detections": list(rows),
        "runtime": {"audit": list(audit or [])},
    }


def test_two_original_temporal_supports_recover_a_physics_candidate():
    module = load_module()
    candidate = provisional(2.0)
    records = [
        record(0, [accepted(0.5)]),
        record(250, [accepted(1.2)]),
        record(500, audit=[candidate]),
    ]
    audit = module.recover(records, configuration(), protocol())
    assert audit[0]["spatial_physics_support"] == 3
    assert audit[0]["temporal_support"] == 2
    assert audit[0]["temporally_recovered"] is True
    assert records[2]["detections"][0]["temporally_recovered"] is True


def test_recovered_candidates_do_not_propagate_support():
    module = load_module()
    records = [
        record(0, [accepted(0.0)]),
        record(250, audit=[provisional(0.8)]),
        record(500, audit=[provisional(1.6)]),
    ]
    audit = module.recover(records, configuration(), protocol())
    assert not any(row["temporally_recovered"] for row in audit)
    assert not records[1]["detections"]
    assert not records[2]["detections"]


def test_temporal_protocol_is_frozen_and_gamma2_independent():
    temporal = configuration()
    holdout = json.loads(
        (ROOT / "vortex_cylinder_wake_re200_temporal_holdout.json").read_text()
    )
    development = json.loads(
        (ROOT / "vortex_cylinder_wake_re150_temporal_development.json").read_text()
    )
    assert temporal["future_case_recalibration_allowed"] is False
    assert temporal["gamma2_used_by_detector"] is False
    assert temporal["recovered_candidates_may_support_other_recoveries"] is False
    assert holdout["solver"]["reynolds_number"] == 200.0
    assert "independent" in holdout["case_role"]
    assert "not independent" in development["case_role"]
