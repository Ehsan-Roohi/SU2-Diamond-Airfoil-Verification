#!/usr/bin/env python3
"""Development-only analytic audit for TSA-SRA-CMCD-v3 shock-conditioned rescue.

This runner changes only the decision path for candidates inside the already
frozen shock-ridge exclusion distance. Far from shocks it preserves the v2
SRA-CMCD decision exactly. Near a shock it does not accept proximity by itself;
it requires strong multi-radius kinematic evidence, pressure-ring support,
compact Q-island scale, and artifact-aware coherence. The rule is development
only and must be frozen before any future unseen CFD holdout is generated.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent


def load_sibling(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def v3_near_shock_pass(candidate: dict, island: dict, winding_support: int, pressure: dict, cfg: dict) -> bool:
    equivalent_radius = math.sqrt(max(float(island["area_cells"]), 0.0) / math.pi)
    return bool(
        bool(island["pass"])
        and int(winding_support) >= int(cfg["minimum_winding_ring_support"])
        and int(pressure["ring_support"]) >= int(cfg["minimum_pressure_ring_support"])
        and equivalent_radius >= float(cfg["minimum_equivalent_q_radius_cells"])
        and float(candidate["rotation_purity"]) >= float(cfg["minimum_rotation_purity"])
        and float(candidate["sign_coherence"]) >= float(cfg["minimum_sign_coherence"])
        and float(candidate["ring_coherence"]) >= float(cfg["minimum_ring_coherence"])
        and float(candidate["radial_to_tangential"]) <= float(cfg["maximum_radial_to_tangential"])
        and float(candidate["scale_persistence"]) >= float(cfg["minimum_scale_persistence"])
        and float(candidate["hessian_compactness"]) >= float(cfg["minimum_hessian_compactness"])
        and float(island["aspect_ratio"]) <= float(cfg["maximum_q_island_aspect_ratio"])
    )


def detect_v3(snapshot: dict, cfg: dict, sra_cfg: dict, v3_cfg: dict, modules: dict):
    artifact = modules["artifact"]
    base = modules["base"]
    sra = modules["sra"]

    fields = artifact.derive_artifact_fields(
        snapshot["x"], snapshot["y"], snapshot["u"], snapshot["v"],
        snapshot["fluid"], cfg["gaussian_sigmas"],
    )
    fields = sra.finish_raster_fields(
        snapshot["x"], snapshot["y"],
        {**fields, "pressure": snapshot["pressure"], "rho": snapshot["rho"]},
        snapshot["fluid"], cfg["gaussian_sigmas"], float(cfg["gamma"]),
    )
    work = {**snapshot, **fields}
    shock_mask, shock_distance = sra.build_shock_ridge_mask(work, sra_cfg)
    work["shock_ridge_mask"] = shock_mask

    raw, threshold = base.all_q_candidates(work, cfg["base_physics_configuration"])
    artifact_valid, artifact_audit = artifact.filter_candidates(
        raw, work, cfg["artifact_veto_configuration"]
    )
    selected, selection = base.select_adaptive(
        artifact_valid, cfg["candidate_budget_configuration"]
    )

    audit: list[dict] = []
    shock_limit = float(sra_cfg["maximum_shock_ridge_distance_cells"])
    near_cfg = v3_cfg["near_shock"]

    for candidate in selected:
        rings = [
            sra.ring_winding_features(work, candidate, float(radius), int(sra_cfg["winding_samples"]))
            for radius in sra_cfg["winding_radii_cells"]
        ]
        winding_support = sum(sra.winding_pass(row, sra_cfg) for row in rings)
        island = sra.closed_q_island(work, candidate, sra_cfg)
        pressure = sra.scale_adaptive_pressure_support(
            sra.pressure_core_support(work, candidate, sra_cfg), island, sra_cfg
        )
        i, j = int(candidate["grid_i"]), int(candidate["grid_j"])
        distance = float(shock_distance[i, j])

        if distance > shock_limit:
            accepted, reason = sra.revised_decision(
                island, winding_support, pressure, distance, sra_cfg
            )
        else:
            if not bool(island["pass"]):
                accepted, reason = False, "open_or_elongated_q_island"
            elif winding_support < int(near_cfg["minimum_winding_ring_support"]):
                accepted, reason = False, "shock_conditioned_insufficient_winding"
            elif v3_near_shock_pass(candidate, island, winding_support, pressure, near_cfg):
                accepted, reason = True, "accepted_shock_conditioned_core"
            else:
                accepted, reason = False, "shock_conditioned_support_failed"

        row = {
            **candidate,
            "winding_support": int(winding_support),
            "q_island": island,
            "q_island_pass": bool(island["pass"]),
            "q_island_closed": bool(island["closed"]),
            "q_island_area_cells": int(island["area_cells"]),
            "q_island_aspect_ratio": float(island["aspect_ratio"]),
            "q_island_analysis_radius_cells": int(island["analysis_radius_cells"]),
            "equivalent_q_radius_cells": math.sqrt(max(float(island["area_cells"]), 0.0) / math.pi),
            "pressure_core": pressure,
            "pressure_pass": bool(pressure["pass"]),
            "shock_ridge_distance_cells": distance,
            "accepted": bool(accepted),
            "rejection_reason": reason,
        }
        audit.append(row)

    # Preserve the established non-shock pair and subordinate-peak rules.
    sra.rescue_corroborated_opposite_sign_pairs(audit, sra_cfg)
    sra.suppress_subordinate_same_sign_peaks(audit, sra_cfg)
    final = [row for row in audit if row["accepted"]]

    diagnostics = {
        "raw_q_candidates": len(raw),
        "artifact_valid_candidates": len(artifact_valid),
        "selected_candidates": len(selected),
        "final_detections": len(final),
        "threshold": threshold,
        "selection": selection,
        "artifact_rejections": dict(Counter(
            row["artifact_reason"] for row in artifact_audit if not row["artifact_accepted"]
        )),
        "v3_rejections": dict(Counter(
            row["rejection_reason"] for row in audit if not row["accepted"]
        )),
        "shock_conditioned_acceptances": sum(
            row["rejection_reason"] == "accepted_shock_conditioned_core" for row in audit
        ),
    }
    return final, {"snapshot": work, "audit": audit, "diagnostics": diagnostics}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--analytic-config", type=Path)
    parser.add_argument("--sra-config", type=Path)
    parser.add_argument("--v3-config", type=Path)
    args = parser.parse_args()

    analytic = load_sibling("v3_analytic", "run_vortex_analytic_positive_control.py")
    base = load_sibling("v3_base", "run_vortex_acb_cmcd.py")
    artifact = load_sibling("v3_artifact", "run_vortex_artifact_aware_acb.py")
    sra = load_sibling("v3_sra", "run_vortex_shock_ridge_aware_su2.py")

    cfg = json.loads((args.analytic_config or ROOT / "vortex_analytic_positive_control.json").read_text())
    sra_cfg = json.loads((args.sra_config or ROOT / "vortex_shock_ridge_aware_cmcd.json").read_text())
    v3_cfg = json.loads((args.v3_config or ROOT / "vortex_shock_conditioned_cmcd_v3_dev.json").read_text())
    if v3_cfg.get("future_case_recalibration_allowed") is not False:
        parser.error("v3 development rule is not frozen")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    modules = {"base": base, "artifact": artifact, "sra": sra}

    results: list[dict] = []
    rows: list[dict] = []
    for definition in analytic.case_definitions(cfg):
        snapshot = analytic.synthetic_case(definition, cfg)
        detections, runtime = detect_v3(snapshot, cfg, sra_cfg, v3_cfg, modules)
        metrics = analytic.score(
            snapshot["truth"], detections, float(cfg["ground_truth_match_radius"])
        )
        result = {
            "case_id": snapshot["case_id"],
            "category": snapshot["category"],
            "metadata": snapshot["metadata"],
            "truth": snapshot["truth"],
            "detections": detections,
            "metrics": metrics,
            "runtime": runtime,
        }
        results.append(result)
        rows.append({
            "case_id": result["case_id"], "category": result["category"],
            **result["metadata"], **runtime["diagnostics"], **metrics,
        })

    summary, original_gates = analytic.aggregate(results, cfg)
    by_id = {row["case_id"]: row for row in results}
    dev = v3_cfg["development_gates"]
    v3_gates = {
        **original_gates,
        "shock_vortex_d0p04_recall": "pass" if by_id["shock_vortex_d0.040"]["metrics"]["recall"] >= float(dev["minimum_recall_at_shock_offset_0p04"]) else "fail",
        "shock_vortex_d0p08_recall": "pass" if by_id["shock_vortex_d0.080"]["metrics"]["recall"] >= float(dev["minimum_recall_at_shock_offset_0p08"]) else "fail",
        "planar_shock_fp": "pass" if by_id["negative_planar_shock"]["metrics"]["false_positive"] <= int(dev["maximum_planar_shock_false_positives"]) else "fail",
        "shock_beads_fp": "pass" if by_id["negative_shock_beads"]["metrics"]["false_positive"] <= int(dev["maximum_shock_bead_false_positives"]) else "fail",
        "pure_shear_fp": "pass" if by_id["negative_pure_shear"]["metrics"]["false_positive"] <= int(dev["maximum_pure_shear_false_positives"]) else "fail",
        "pure_strain_fp": "pass" if by_id["negative_pure_strain"]["metrics"]["false_positive"] <= int(dev["maximum_pure_strain_false_positives"]) else "fail",
    }

    shock_ids = [row["case_id"] for row in results if row["category"] == "shock_vortex"]
    negative_ids = [row["case_id"] for row in results if row["category"] == "negative"]
    analytic.draw_cases(
        output / "analytic_v3_shock_vortex_physical.png",
        results, shock_ids,
        "TSA-SRA-CMCD-v3 shock-conditioned development: truth (+), detections (green)",
    )
    analytic.draw_cases(
        output / "analytic_v3_negative_controls_physical.png",
        results, negative_ids,
        "TSA-SRA-CMCD-v3 adversarial negative controls",
    )

    serializable = [{k: value for k, value in row.items() if k != "runtime"} for row in results]
    report = {
        "schema_version": 1,
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "method_name": v3_cfg["method_name"],
        "development_only": True,
        "v3_configuration": v3_cfg,
        "cases": serializable,
        "category_summary": summary,
        "gates": v3_gates,
        "claim_gate": "v3_shock_conditioned_development_pass" if all(v == "pass" for v in v3_gates.values()) else "v3_shock_conditioned_development_failed",
        "limitations": [
            "The v3 rule was defined using the existing analytic shock-vortex sweep and retrospective SU2 failure; these cases are development evidence only.",
            "No unseen CFD holdout is evaluated here.",
            "The analytic shock-vortex field is a controlled superposition, not a full compressible shock-vortex interaction solution."
        ],
    }
    (output / "analytic_v3_shock_conditioned_report.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    analytic.write_csv(
        output / "analytic_v3_shock_conditioned_cases.csv",
        rows,
        sorted({key for row in rows for key in row}),
    )

    print("V3_STATUS=completed")
    print(f"V3_CLAIM_GATE={report['claim_gate']}")
    for case_id in ["shock_vortex_d0.040", "shock_vortex_d0.080", "shock_vortex_d0.120", "negative_planar_shock", "negative_shock_beads"]:
        m = by_id[case_id]["metrics"]
        print(f"V3_CASE={case_id} TP={m['true_positive']} FP={m['false_positive']} FN={m['false_negative']} P={m['precision']:.6f} R={m['recall']:.6f}")
    return 0 if report["claim_gate"] == "v3_shock_conditioned_development_pass" else 5


if __name__ == "__main__":
    raise SystemExit(main())
