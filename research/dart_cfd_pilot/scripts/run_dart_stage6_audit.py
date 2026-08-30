#!/usr/bin/env python3
"""Audit Stage-5 references and Stage-4 matches without tuning to a pass.

Stage 6 removes raw-reference frames that fail the independent vorticity
consistency gate, restricts both methods to DART's exact physical field of
view, and reports sensitivity to three predeclared reference-persistence
definitions. It distinguishes sparse, precise localization from comprehensive
vortex detection.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_pixels(width: int, height: int, bounds: list[float]) -> list[int]:
    left, top, right, bottom = bounds
    return [
        round(left * width),
        round(top * height),
        round(right * width),
        round(bottom * height),
    ]


def physical_field_of_view(stage3_report: dict, stage3_config: dict) -> dict:
    width, height = (int(value) for value in stage3_report["source_video_size"])
    plot_left, plot_top, plot_right, plot_bottom = normalized_pixels(
        width, height, stage3_config["plot_bounds_normalized"]
    )
    crop_left, crop_top, crop_right, crop_bottom = (
        int(value) for value in stage3_report["analysis_crop_pixels"]
    )
    xlim = [float(value) for value in stage3_config["physical_xlim"]]
    ylim = [float(value) for value in stage3_config["physical_ylim"]]

    def x_value(pixel: int) -> float:
        fraction = (pixel - plot_left) / max(plot_right - plot_left, 1)
        return xlim[0] + fraction * (xlim[1] - xlim[0])

    def y_value(pixel: int) -> float:
        fraction = (pixel - plot_top) / max(plot_bottom - plot_top, 1)
        return ylim[1] - fraction * (ylim[1] - ylim[0])

    return {
        "xlim": [x_value(crop_left), x_value(crop_right)],
        "ylim": [y_value(crop_bottom), y_value(crop_top)],
        "source_video_size": [width, height],
        "analysis_crop_pixels": [
            crop_left,
            crop_top,
            crop_right,
            crop_bottom,
        ],
    }


def inside_field_of_view(row: dict[str, str], field_of_view: dict) -> bool:
    x_value = float(row["x_physical"])
    y_value = float(row["y_physical"])
    return (
        field_of_view["xlim"][0] <= x_value <= field_of_view["xlim"][1]
        and field_of_view["ylim"][0] <= y_value <= field_of_view["ylim"][1]
    )


def summarize_reference_tracks(rows: list[dict[str, str]]) -> dict[str, dict]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["reference_id"]].append(row)
    summaries = {}
    for reference_id, values in grouped.items():
        values.sort(key=lambda row: int(row["frame_index"]))
        first, last = values[0], values[-1]
        first_frame = int(first["frame_index"])
        last_frame = int(last["frame_index"])
        span = last_frame - first_frame + 1
        summaries[reference_id] = {
            "reference_id": reference_id,
            "observations": len(values),
            "first_frame": first_frame,
            "last_frame": last_frame,
            "lifetime": float(last["time"]) - float(first["time"]),
            "displacement": math.hypot(
                float(last["x_physical"]) - float(first["x_physical"]),
                float(last["y_physical"]) - float(first["y_physical"]),
            ),
            "continuity": len(values) / span,
        }
    return summaries


def qualifies(summary: dict, definition: dict) -> bool:
    return all(
        (
            summary["observations"] >= definition["minimum_observations"],
            summary["lifetime"] >= definition["minimum_lifetime"],
            summary["displacement"] >= definition["minimum_displacement"],
            summary["continuity"] >= definition["minimum_continuity"],
        )
    )


def definition_metrics(
    definition: dict,
    reference_rows: list[dict[str, str]],
    summaries: dict[str, dict],
    matches: list[dict[str, str]],
) -> dict:
    qualified_ids = {
        reference_id
        for reference_id, summary in summaries.items()
        if qualifies(summary, definition)
    }
    eligible_rows = [
        row for row in reference_rows if row["reference_id"] in qualified_ids
    ]
    eligible_keys = {
        (int(row["frame_index"]), row["reference_id"]) for row in eligible_rows
    }
    matched = [
        row
        for row in matches
        if (int(row["frame_index"]), row["reference_id"]) in eligible_keys
    ]
    matched_ids = {row["reference_id"] for row in matched}
    track_coverage = len(matched_ids) / len(qualified_ids) if qualified_ids else 0.0
    observation_coverage = len(matched) / len(eligible_rows) if eligible_rows else 0.0
    return {
        "name": definition["name"],
        "criteria": {
            key: value for key, value in definition.items() if key != "name"
        },
        "qualified_reference_tracks": len(qualified_ids),
        "eligible_reference_observations": len(eligible_rows),
        "matched_reference_tracks": len(matched_ids),
        "matched_reference_observations": len(matched),
        "track_identity_coverage": track_coverage,
        "observation_coverage": observation_coverage,
    }


def matched_strength_ranks(
    reference_rows: list[dict[str, str]], matches: list[dict[str, str]]
) -> list[dict]:
    by_frame: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in reference_rows:
        by_frame[int(row["frame_index"])].append(row)
    matched_keys = {
        (int(row["frame_index"]), row["reference_id"]): row for row in matches
    }
    output = []
    for frame_index, rows in sorted(by_frame.items()):
        ordered = sorted(
            rows,
            key=lambda row: abs(float(row["omega"])) * float(row["lambda_ci"]),
            reverse=True,
        )
        for rank, row in enumerate(ordered, start=1):
            key = (frame_index, row["reference_id"])
            if key not in matched_keys:
                continue
            output.append(
                {
                    "frame_index": frame_index,
                    "track_id": int(matched_keys[key]["track_id"]),
                    "reference_id": row["reference_id"],
                    "strength_rank_in_frame": rank,
                    "reference_cores_in_frame": len(ordered),
                    "center_distance": float(matched_keys[key]["center_distance"]),
                    "x_physical": float(row["x_physical"]),
                    "y_physical": float(row["y_physical"]),
                    "omega": float(row["omega"]),
                    "lambda_ci": float(row["lambda_ci"]),
                }
            )
    return output


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def classify_claim(
    precision: float,
    definitions: list[dict],
    temporal_uniqueness_pass: bool,
    config: dict,
) -> tuple[str, dict]:
    primary = next(
        item for item in definitions if item["name"] == config["primary_definition"]
    )
    comprehensive = (
        temporal_uniqueness_pass
        and primary["track_identity_coverage"]
        >= config["minimum_comprehensive_track_coverage"]
        and primary["observation_coverage"]
        >= config["minimum_comprehensive_observation_coverage"]
    )
    sparse = (
        precision >= config["minimum_sparse_localization_precision"]
        and max(item["observation_coverage"] for item in definitions)
        <= config["maximum_sparse_observation_coverage"]
    )
    if comprehensive:
        claim = "physics_validated_comprehensive_vortex_tracking"
    elif sparse:
        claim = "diagnostic_high_precision_sparse_vortex_localization"
    else:
        claim = "off_the_shelf_vortex_localization_not_validated"
    return claim, {
        "sparse_localization": sparse,
        "comprehensive_tracking": comprehensive,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage3-dir", type=Path, required=True)
    parser.add_argument("--stage5-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--stage3-config", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("results/stage6-manual"))
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    config = json.loads((args.config or root / "dart_stage6.json").read_text())
    stage3_config = json.loads(
        (args.stage3_config or root / "dart_stage3.json").read_text()
    )
    stage3_dir = args.stage3_dir.resolve()
    stage5_dir = args.stage5_dir.resolve()
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    stage3_report_path = stage3_dir / "stage3_report.json"
    stage5_report_path = stage5_dir / "stage5_report.json"
    stage4_report_path = stage5_dir / "stage4_report.json"
    reference_path = stage5_dir / "stage5_reference.csv"
    matches_path = stage5_dir / "stage4_reference_matches.csv"
    required = [
        stage3_report_path,
        stage5_report_path,
        stage4_report_path,
        reference_path,
        matches_path,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        parser.error("missing Stage-6 input files: " + ", ".join(missing))

    stage3_report = json.loads(stage3_report_path.read_text())
    stage5_report = json.loads(stage5_report_path.read_text())
    stage4_report = json.loads(stage4_report_path.read_text())
    references = read_csv(reference_path)
    matches = read_csv(matches_path)
    field_of_view = physical_field_of_view(stage3_report, stage3_config)
    valid_frames = {
        int(frame["frame_index"])
        for frame in stage5_report["per_frame"]
        if frame.get("vorticity_correlation") is not None
        and abs(float(frame["vorticity_correlation"]))
        >= config["minimum_vorticity_correlation"]
    }
    comparable = [
        row
        for row in references
        if int(row["frame_index"]) in valid_frames
        and inside_field_of_view(row, field_of_view)
    ]
    comparable_keys = {
        (int(row["frame_index"]), row["reference_id"]) for row in comparable
    }
    comparable_matches = [
        row
        for row in matches
        if (int(row["frame_index"]), row["reference_id"]) in comparable_keys
    ]
    summaries = summarize_reference_tracks(comparable)
    definitions = [
        definition_metrics(
            definition,
            comparable,
            summaries,
            comparable_matches,
        )
        for definition in config["reference_definitions"]
    ]

    reference_metrics = stage4_report["reference_metrics"]
    canonical_detections = int(reference_metrics["canonical_detection_rows"])
    true_positive = len(comparable_matches)
    precision = true_positive / canonical_detections if canonical_detections else 0.0
    temporal_uniqueness_pass = (
        stage4_report["gates"]["temporal_uniqueness"] == "pass"
    )
    claim_gate, classification = classify_claim(
        precision, definitions, temporal_uniqueness_pass, config
    )
    ranks = matched_strength_ranks(comparable, comparable_matches)
    rank_values = [row["strength_rank_in_frame"] for row in ranks]

    write_csv(
        output_dir / "stage6_definition_sensitivity.csv",
        [
            {
                "definition": item["name"],
                **item["criteria"],
                "qualified_reference_tracks": item["qualified_reference_tracks"],
                "eligible_reference_observations": item["eligible_reference_observations"],
                "matched_reference_tracks": item["matched_reference_tracks"],
                "matched_reference_observations": item["matched_reference_observations"],
                "track_identity_coverage": item["track_identity_coverage"],
                "observation_coverage": item["observation_coverage"],
            }
            for item in definitions
        ],
        [
            "definition",
            "minimum_observations",
            "minimum_lifetime",
            "minimum_displacement",
            "minimum_continuity",
            "qualified_reference_tracks",
            "eligible_reference_observations",
            "matched_reference_tracks",
            "matched_reference_observations",
            "track_identity_coverage",
            "observation_coverage",
        ],
    )
    write_csv(
        output_dir / "stage6_matched_core_ranks.csv",
        ranks,
        [
            "frame_index",
            "track_id",
            "reference_id",
            "strength_rank_in_frame",
            "reference_cores_in_frame",
            "center_distance",
            "x_physical",
            "y_physical",
            "omega",
            "lambda_ci",
        ],
    )

    report = {
        "schema_version": 1,
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": stage5_report.get("case_id"),
        "inputs": {
            "stage3_directory": str(stage3_dir),
            "stage5_directory": str(stage5_dir),
            "sha256": {path.name: sha256(path) for path in required},
        },
        "field_of_view": field_of_view,
        "valid_reference_frames": len(valid_frames),
        "excluded_reference_frames": sorted(
            set(range(int(stage5_report["frames"]))) - valid_frames
        ),
        "raw_reference_rows": len(references),
        "comparable_reference_rows": len(comparable),
        "canonical_dart_observations": canonical_detections,
        "matched_dart_observations": true_positive,
        "localization_precision": precision,
        "definition_sensitivity": definitions,
        "matched_strength_rank": {
            "count": len(rank_values),
            "minimum": min(rank_values) if rank_values else None,
            "median": statistics.median(rank_values) if rank_values else None,
            "maximum": max(rank_values) if rank_values else None,
        },
        "classification": classification,
        "gates": {
            "technical_execution": "pass",
            "common_field_of_view": "pass",
            "invalid_reference_frame_exclusion": "pass",
            "definition_sensitivity": "pass",
            "sparse_localization": (
                "pass" if classification["sparse_localization"] else "fail"
            ),
            "comprehensive_tracking": (
                "pass" if classification["comprehensive_tracking"] else "fail"
            ),
            "publication_claim": (
                "pass"
                if claim_gate == "physics_validated_comprehensive_vortex_tracking"
                else "fail"
            ),
        },
        "claim_gate": claim_gate,
        "limitations": [
            "This is one two-dimensional Mach-3 alpha-40 case; it cannot establish cross-case generalization.",
            "Raw centres are threshold-defined local vortex indicators, not uniquely defined material vortices.",
            "High localization precision does not imply comprehensive vortex detection when observation coverage is sparse.",
            "A publication-level detector claim requires multiple cases and either domain adaptation or comparison with additional vision baselines.",
        ],
    }
    report_path = output_dir / "stage6_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    primary = next(
        item for item in definitions if item["name"] == config["primary_definition"]
    )
    print("STAGE6_STATUS=completed")
    print(f"STAGE6_VALID_REFERENCE_FRAMES={len(valid_frames)}")
    print(f"STAGE6_COMPARABLE_REFERENCE_ROWS={len(comparable)}")
    print(f"STAGE6_LOCALIZATION_PRECISION={precision:.12g}")
    print(f"STAGE6_PRIMARY_TRACK_COVERAGE={primary['track_identity_coverage']:.12g}")
    print(f"STAGE6_PRIMARY_OBSERVATION_COVERAGE={primary['observation_coverage']:.12g}")
    print(f"STAGE6_CLAIM_GATE={claim_gate}")
    print(f"STAGE6_REPORT={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
