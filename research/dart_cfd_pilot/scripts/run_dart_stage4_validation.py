#!/usr/bin/env python3
"""Audit Stage-3 DART tracks and optionally validate them against CFD references.

Stage 4 is deliberately CPU-only. It first removes duplicate track identities,
then applies stricter persistence criteria. A publication-level pass is
impossible unless a raw-field reference CSV is supplied.
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


TRACK_COLUMNS = {
    "frame_index",
    "time",
    "track_id",
    "score",
    "box_x1",
    "box_y1",
    "box_x2",
    "box_y2",
    "x_physical",
    "y_physical",
}


def read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        fields = set(reader.fieldnames or [])
        missing = sorted(required - fields)
        if missing:
            raise ValueError(f"{path} is missing columns: {', '.join(missing)}")
        return list(reader)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def box_iou(a: dict[str, str], b: dict[str, str]) -> float:
    ax1, ay1, ax2, ay2 = (float(a[key]) for key in ("box_x1", "box_y1", "box_x2", "box_y2"))
    bx1, by1, bx2, by2 = (float(b[key]) for key in ("box_x1", "box_y1", "box_x2", "box_y2"))
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0.0, min(ay2, by2) - max(ay1, by1)
    )
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


def center_distance(a: dict[str, str], b: dict[str, str]) -> float:
    return math.hypot(
        float(a["x_physical"]) - float(b["x_physical"]),
        float(a["y_physical"]) - float(b["y_physical"]),
    )


def grouped_tracks(rows: list[dict[str, str]]) -> dict[int, list[dict[str, str]]]:
    grouped: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["track_id"])].append(row)
    for values in grouped.values():
        values.sort(key=lambda row: int(row["frame_index"]))
    return dict(grouped)


def summarize_track(track_id: int, rows: list[dict[str, str]], config: dict) -> dict:
    first, last = rows[0], rows[-1]
    first_frame = int(first["frame_index"])
    last_frame = int(last["frame_index"])
    span_frames = last_frame - first_frame + 1
    lifetime = float(last["time"]) - float(first["time"])
    displacement = center_distance(first, last)
    mean_score = statistics.fmean(float(row["score"]) for row in rows)
    continuity = len(rows) / span_frames
    strict_qualified = all(
        (
            len(rows) >= config["minimum_observations"],
            lifetime >= config["minimum_lifetime"],
            displacement >= config["minimum_displacement"],
            continuity >= config["minimum_continuity"],
            mean_score >= config["minimum_mean_score"],
        )
    )
    return {
        "track_id": track_id,
        "observations": len(rows),
        "first_frame": first_frame,
        "last_frame": last_frame,
        "span_frames": span_frames,
        "lifetime": lifetime,
        "displacement": displacement,
        "continuity": continuity,
        "mean_score": mean_score,
        "strict_qualified_before_deduplication": strict_qualified,
    }


class DisjointSet:
    def __init__(self, values: list[int]):
        self.parent = {value: value for value in values}

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def duplicate_audit(grouped: dict[int, list[dict[str, str]]], config: dict) -> tuple[list[dict], dict[int, int]]:
    track_ids = sorted(grouped)
    sets = DisjointSet(track_ids)
    duplicate_pairs = []
    by_frame = {
        track_id: {int(row["frame_index"]): row for row in rows}
        for track_id, rows in grouped.items()
    }
    for index, left in enumerate(track_ids):
        for right in track_ids[index + 1 :]:
            shared = sorted(set(by_frame[left]) & set(by_frame[right]))
            if len(shared) < config["duplicate_minimum_shared_frames"]:
                continue
            distances = [center_distance(by_frame[left][frame], by_frame[right][frame]) for frame in shared]
            overlaps = [box_iou(by_frame[left][frame], by_frame[right][frame]) for frame in shared]
            median_distance = statistics.median(distances)
            median_iou = statistics.median(overlaps)
            duplicate = (
                median_distance <= config["duplicate_median_center_distance"]
                and median_iou >= config["duplicate_median_iou"]
            )
            if duplicate:
                sets.union(left, right)
            duplicate_pairs.append(
                {
                    "track_a": left,
                    "track_b": right,
                    "shared_frames": len(shared),
                    "median_center_distance": median_distance,
                    "median_box_iou": median_iou,
                    "duplicate": duplicate,
                }
            )

    components: dict[int, list[int]] = defaultdict(list)
    for track_id in track_ids:
        components[sets.find(track_id)].append(track_id)
    summaries = {
        track_id: summarize_track(track_id, grouped[track_id], config)
        for track_id in track_ids
    }
    canonical_by_track: dict[int, int] = {}
    for members in components.values():
        canonical = max(
            members,
            key=lambda track_id: (
                summaries[track_id]["strict_qualified_before_deduplication"],
                summaries[track_id]["observations"],
                summaries[track_id]["mean_score"],
                -track_id,
            ),
        )
        canonical_by_track.update({track_id: canonical for track_id in members})
    return duplicate_pairs, canonical_by_track


def audit_tracks(rows: list[dict[str, str]], config: dict) -> tuple[list[dict], dict]:
    grouped = grouped_tracks(rows)
    duplicate_pairs, canonical_by_track = duplicate_audit(grouped, config)
    summaries = []
    for track_id in sorted(grouped):
        summary = summarize_track(track_id, grouped[track_id], config)
        canonical = canonical_by_track[track_id]
        summary.update(
            {
                "canonical_track_id": canonical,
                "duplicate_identity": canonical != track_id,
                "unique_qualified": (
                    summary["strict_qualified_before_deduplication"]
                    and canonical == track_id
                ),
            }
        )
        summaries.append(summary)

    unique_qualified = [row for row in summaries if row["unique_qualified"]]
    duplicate_components = sorted(
        {
            tuple(sorted(track for track, canonical in canonical_by_track.items() if canonical == root or canonical_by_track[track] == root))
            for root in set(canonical_by_track.values())
            if sum(1 for canonical in canonical_by_track.values() if canonical == root) > 1
        }
    )
    return summaries, {
        "input_track_identities": len(grouped),
        "duplicate_pairs": [pair for pair in duplicate_pairs if pair["duplicate"]],
        "duplicate_components": [list(component) for component in duplicate_components],
        "unique_strictly_qualified_tracks": len(unique_qualified),
        "unique_strictly_qualified_track_ids": [row["track_id"] for row in unique_qualified],
    }


def canonical_rows(rows: list[dict[str, str]], summaries: list[dict]) -> list[dict[str, str]]:
    canonical = {
        row["track_id"] for row in summaries if not row["duplicate_identity"]
    }
    return [row for row in rows if int(row["track_id"]) in canonical]


def validate_reference(
    detection_rows: list[dict[str, str]], reference_rows: list[dict[str, str]], config: dict
) -> tuple[dict, list[dict]]:
    detections_by_frame: dict[int, list[dict[str, str]]] = defaultdict(list)
    references_by_frame: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in detection_rows:
        detections_by_frame[int(row["frame_index"])].append(row)
    for row in reference_rows:
        references_by_frame[int(row["frame_index"])].append(row)

    matches = []
    true_positive = 0
    total_detections = len(detection_rows)
    total_references = len(reference_rows)
    for frame in sorted(set(detections_by_frame) | set(references_by_frame)):
        candidates = []
        for detection in detections_by_frame[frame]:
            for reference in references_by_frame[frame]:
                distance = center_distance(detection, reference)
                if distance <= config["reference_match_distance"]:
                    candidates.append((distance, detection, reference))
        used_tracks: set[int] = set()
        used_references: set[str] = set()
        for distance, detection, reference in sorted(candidates, key=lambda item: item[0]):
            track_id = int(detection["track_id"])
            reference_id = reference["reference_id"]
            if track_id in used_tracks or reference_id in used_references:
                continue
            used_tracks.add(track_id)
            used_references.add(reference_id)
            true_positive += 1
            matches.append(
                {
                    "frame_index": frame,
                    "track_id": track_id,
                    "reference_id": reference_id,
                    "center_distance": distance,
                }
            )

    false_positive = total_detections - true_positive
    false_negative = total_references - true_positive
    precision = true_positive / total_detections if total_detections else 0.0
    recall = true_positive / total_references if total_references else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    center_rmse = (
        math.sqrt(statistics.fmean(match["center_distance"] ** 2 for match in matches))
        if matches
        else None
    )

    matched_by_reference: dict[str, list[dict]] = defaultdict(list)
    for match in matches:
        matched_by_reference[match["reference_id"]].append(match)
    id_switches = 0
    for reference_matches in matched_by_reference.values():
        ordered = sorted(reference_matches, key=lambda item: item["frame_index"])
        id_switches += sum(
            left["track_id"] != right["track_id"]
            for left, right in zip(ordered, ordered[1:])
        )

    passed = all(
        (
            precision >= config["reference_minimum_precision"],
            recall >= config["reference_minimum_recall"],
            f1 >= config["reference_minimum_f1"],
            center_rmse is not None,
            center_rmse is not None and center_rmse <= config["reference_maximum_center_rmse"],
            id_switches <= config["reference_maximum_id_switches"],
        )
    )
    return {
        "reference_rows": total_references,
        "canonical_detection_rows": total_detections,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "center_rmse": center_rmse,
        "id_switches": id_switches,
        "pass": passed,
    }, matches


def write_dict_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage3-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--reference-csv", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("results/stage4-manual"))
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    config_path = args.config or root / "dart_stage4.json"
    config = json.loads(config_path.read_text())
    stage3_dir = args.stage3_dir.resolve()
    tracks_path = stage3_dir / "stage3_tracks.csv"
    report_path = stage3_dir / "stage3_report.json"
    if not tracks_path.is_file() or not report_path.is_file():
        parser.error(f"Stage-3 report and tracks were not found under {stage3_dir}")
    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    stage3_report = json.loads(report_path.read_text())
    rows = read_csv(tracks_path, TRACK_COLUMNS)
    summaries, audit = audit_tracks(rows, config)
    unique_rows = canonical_rows(rows, summaries)
    stage3_qualified = int(stage3_report.get("temporal_summary", {}).get("qualified_tracks", 0))
    strict_unique = audit["unique_strictly_qualified_tracks"]
    temporal_uniqueness_pass = strict_unique >= config["minimum_unique_qualified_tracks"]

    reference_metrics = None
    reference_matches: list[dict] = []
    if args.reference_csv is not None:
        required = set(config["reference_required_columns"])
        reference_rows = read_csv(args.reference_csv.resolve(), required)
        reference_metrics, reference_matches = validate_reference(unique_rows, reference_rows, config)
        write_dict_csv(
            output_dir / "stage4_reference_matches.csv",
            reference_matches,
            ["frame_index", "track_id", "reference_id", "center_distance"],
        )

    physical_state = "not_run_raw_field_reference_required"
    if reference_metrics is not None:
        physical_state = "pass" if reference_metrics["pass"] else "fail"
    publication_pass = bool(reference_metrics and reference_metrics["pass"] and temporal_uniqueness_pass)
    if publication_pass:
        claim_gate = "physics_validated_vortex_tracking"
    elif reference_metrics is not None:
        claim_gate = "physical_validation_failed"
    else:
        claim_gate = "diagnostic_signal_present_raw_field_validation_required"

    report = {
        "schema_version": 1,
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": stage3_report.get("case_id"),
        "stage3_project_commit": stage3_report.get("project_commit"),
        "stage3_directory": str(stage3_dir),
        "input_sha256": {
            "stage3_report.json": sha256(report_path),
            "stage3_tracks.csv": sha256(tracks_path),
        },
        "stage3_claim_gate": stage3_report.get("claim_gate"),
        "stage3_qualified_tracks": stage3_qualified,
        "track_observations": len(rows),
        "accepted_consensus_detections": stage3_report.get("accepted_consensus_detections"),
        "tracking_utilization_fraction": (
            len(rows) / stage3_report["accepted_consensus_detections"]
            if stage3_report.get("accepted_consensus_detections")
            else None
        ),
        "audit": audit,
        "tracks": summaries,
        "reference_metrics": reference_metrics,
        "gates": {
            "technical_execution": "pass",
            "duplicate_identity_audit": "pass",
            "temporal_uniqueness": "pass" if temporal_uniqueness_pass else "fail",
            "physical_validation": physical_state,
            "publication_claim": "pass" if publication_pass else "fail",
        },
        "claim_gate": claim_gate,
        "stage3_frequency_proxy_usable": False,
        "limitations": [
            "Duplicate identities and short tracks are removed before counting physical structures.",
            "The Stage-3 inter-track-birth frequency proxy is blocked from scientific use.",
            "Raw-field reference vortices must be generated independently from the visualization raster.",
            "Reference sensitivity should include signed vorticity plus swirling strength or Rortex.",
        ],
    }
    report_output = output_dir / "stage4_report.json"
    report_output.write_text(json.dumps(report, indent=2) + "\n")
    write_dict_csv(
        output_dir / "stage4_track_audit.csv",
        summaries,
        [
            "track_id",
            "canonical_track_id",
            "duplicate_identity",
            "observations",
            "first_frame",
            "last_frame",
            "span_frames",
            "lifetime",
            "displacement",
            "continuity",
            "mean_score",
            "strict_qualified_before_deduplication",
            "unique_qualified",
        ],
    )
    print("STAGE4_STATUS=completed")
    print(f"STAGE4_STAGE3_QUALIFIED={stage3_qualified}")
    print(f"STAGE4_UNIQUE_STRICT_QUALIFIED={strict_unique}")
    print(f"STAGE4_DUPLICATE_COMPONENTS={len(audit['duplicate_components'])}")
    print(f"STAGE4_PHYSICAL_VALIDATION={physical_state}")
    print(f"STAGE4_CLAIM_GATE={claim_gate}")
    print(f"STAGE4_REPORT={report_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
