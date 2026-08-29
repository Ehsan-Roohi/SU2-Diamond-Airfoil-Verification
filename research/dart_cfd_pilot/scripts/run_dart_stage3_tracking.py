#!/usr/bin/env python3
"""Run the Stage-3 temporal vortex-tracking decision gate.

This runner deliberately separates three levels of evidence:

1. open-vocabulary DART detections;
2. prompt-consensus detections that overlap a high-chroma vorticity raster proxy;
3. persistent ByteTrack trajectories.

The raster proxy is not a raw-field vortex criterion. It only rejects obvious
semantic detections on neutral plot regions. Publication claims still require
reference masks derived from the numerical vorticity/velocity arrays.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def resolve_source_video(
    configured_video: Path,
    *,
    explicit_video: Path | None = None,
    explicit_archive: Path | None = None,
    search_roots: list[Path] | None = None,
    cache_dir: Path | None = None,
    archive_basename: str | None = None,
) -> tuple[Path, str]:
    """Resolve a moved video or extract it from its recorded products archive.

    The resolver never chooses between multiple matches. Ambiguity must be
    removed with ``--source-video`` or ``--source-archive`` so that a different
    CFD case cannot be selected silently.
    """
    expected_basename = configured_video.name
    if explicit_video is not None:
        if not explicit_video.is_file():
            raise FileNotFoundError(f"explicit Stage-3 video not found: {explicit_video}")
        return explicit_video.resolve(), "explicit_video"
    roots = [root.resolve() for root in (search_roots or []) if root.is_dir()]
    if explicit_archive is None:
        if configured_video.is_file():
            return configured_video.resolve(), "configured_video"
        video_matches = sorted(
            {
                candidate.resolve()
                for root in roots
                for candidate in root.rglob(expected_basename)
                if candidate.is_file()
            }
        )
        if len(video_matches) == 1:
            return video_matches[0], "discovered_video"
        if len(video_matches) > 1:
            rendered = "\n".join(f"  - {path}" for path in video_matches)
            raise ValueError(
                "multiple Stage-3 videos matched; set --source-video explicitly:\n"
                + rendered
            )

    archives: list[Path] = []
    if explicit_archive is not None:
        if not explicit_archive.is_file():
            raise FileNotFoundError(
                f"explicit Stage-3 products archive not found: {explicit_archive}"
            )
        archives = [explicit_archive.resolve()]
    elif archive_basename:
        archives = sorted(
            {
                candidate.resolve()
                for root in roots
                for candidate in root.rglob(archive_basename)
                if candidate.is_file()
            }
        )

    archive_members: list[tuple[Path, str]] = []
    for archive in archives:
        try:
            with zipfile.ZipFile(archive) as bundle:
                archive_members.extend(
                    (archive, member)
                    for member in bundle.namelist()
                    if Path(member).name == expected_basename and not member.endswith("/")
                )
        except zipfile.BadZipFile as exc:
            raise ValueError(f"invalid Stage-3 products archive: {archive}") from exc

    if len(archive_members) > 1:
        rendered = "\n".join(
            f"  - {archive}::{member}" for archive, member in archive_members
        )
        raise ValueError(
            "multiple archived Stage-3 videos matched; set --source-archive explicitly:\n"
            + rendered
        )
    if len(archive_members) == 1:
        archive, member = archive_members[0]
        destination_dir = (cache_dir or Path.cwd() / "stage3-input-cache").resolve()
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / expected_basename
        temporary = destination.with_suffix(destination.suffix + ".part")
        with zipfile.ZipFile(archive) as bundle, bundle.open(member) as source, temporary.open(
            "wb"
        ) as target:
            shutil.copyfileobj(source, target)
        os.replace(temporary, destination)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError(f"extracted Stage-3 video is empty: {destination}")
        return destination, f"extracted_archive:{archive}::{member}"

    searched = ", ".join(str(root) for root in roots) or "no existing search root"
    raise FileNotFoundError(
        f"Stage-3 source video {expected_basename!r} was not found. "
        f"Searched: {searched}. Provide --source-video or --source-archive."
    )


def git_head(repo: Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def box_iou(a: list[float], b: list[float]) -> float:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def weighted_box(members: list[dict]) -> list[float]:
    total = sum(max(item["score"], 1e-12) for item in members)
    return [
        sum(item["box_xyxy"][index] * max(item["score"], 1e-12) for item in members)
        / total
        for index in range(4)
    ]


def cluster_detections(detections: list[dict], iou_threshold: float) -> list[dict]:
    """Greedily cluster overlapping detections from different prompt synonyms."""
    clusters: list[list[dict]] = []
    for detection in sorted(detections, key=lambda item: item["score"], reverse=True):
        best_index = None
        best_iou = 0.0
        for index, members in enumerate(clusters):
            overlap = box_iou(detection["box_xyxy"], weighted_box(members))
            if overlap >= iou_threshold and overlap > best_iou:
                best_index = index
                best_iou = overlap
        if best_index is None:
            clusters.append([detection])
        else:
            clusters[best_index].append(detection)

    output = []
    for members in clusters:
        prompts = sorted({item["prompt"] for item in members})
        per_prompt_best = {
            prompt: max(
                item["score"] for item in members if item["prompt"] == prompt
            )
            for prompt in prompts
        }
        output.append(
            {
                "box_xyxy": weighted_box(members),
                "score": sum(per_prompt_best.values()) / len(per_prompt_best),
                "maximum_score": max(per_prompt_best.values()),
                "prompts": prompts,
                "prompt_support": len(prompts),
                "members": members,
            }
        )
    return sorted(output, key=lambda item: item["score"], reverse=True)


def normalized_crop_pixels(
    width: int, height: int, bounds: list[float]
) -> list[int]:
    if len(bounds) != 4:
        raise ValueError("normalized crop must contain four values")
    left, top, right, bottom = bounds
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError(f"invalid normalized crop {bounds}")
    return [
        round(left * width),
        round(top * height),
        round(right * width),
        round(bottom * height),
    ]


def raster_proxy_fraction(rgb_crop, box: list[float], threshold: float) -> float:
    import numpy as np

    height, width = rgb_crop.shape[:2]
    x1 = max(0, min(width, math.floor(box[0])))
    y1 = max(0, min(height, math.floor(box[1])))
    x2 = max(0, min(width, math.ceil(box[2])))
    y2 = max(0, min(height, math.ceil(box[3])))
    if x2 <= x1 or y2 <= y1:
        return 0.0
    patch = rgb_crop[y1:y2, x1:x2].astype(np.float32) / 255.0
    chroma = patch.max(axis=2) - patch.min(axis=2)
    return float((chroma >= threshold).mean())


def source_to_physical(
    x_source: float,
    y_source: float,
    source_size: tuple[int, int],
    plot_bounds: list[float],
    xlim: list[float],
    ylim: list[float],
) -> tuple[float, float]:
    width, height = source_size
    plot_left, plot_top, plot_right, plot_bottom = normalized_crop_pixels(
        width, height, plot_bounds
    )
    nx = (x_source - plot_left) / max(plot_right - plot_left, 1)
    ny = (y_source - plot_top) / max(plot_bottom - plot_top, 1)
    x_value = xlim[0] + nx * (xlim[1] - xlim[0])
    y_value = ylim[1] - ny * (ylim[1] - ylim[0])
    return x_value, y_value


def validate_config(config: dict) -> None:
    if len(config["prompts"]) < 2 or len(config["prompts"]) != len(
        set(config["prompts"])
    ):
        raise ValueError("Stage 3 requires at least two unique prompts")
    normalized_crop_pixels(100, 100, config["analysis_crop_normalized"])
    normalized_crop_pixels(100, 100, config["plot_bounds_normalized"])
    if config["minimum_prompt_support"] < 2:
        raise ValueError("minimum_prompt_support must be at least two")
    if not 0 < config["maximum_box_area_fraction"] < 0.25:
        raise ValueError("maximum_box_area_fraction must be in (0, 0.25)")
    if config["snapshot_dt"] <= 0:
        raise ValueError("snapshot_dt must be positive")


def draw_overlay(frame, tracks, metadata_by_track, cv2):
    output = frame.copy()
    for track in tracks:
        box = [int(value) for value in track.box_xyxy]
        meta = metadata_by_track.get(track.track_id, {})
        support = meta.get("prompt_support", 0)
        proxy = meta.get("raster_proxy_fraction", 0.0)
        label = f"#{track.track_id} vortex s={track.score:.2f} p={support} r={proxy:.2f}"
        cv2.rectangle(output, (box[0], box[1]), (box[2], box[3]), (0, 120, 255), 2)
        cv2.putText(
            output,
            label,
            (box[0], max(16, box[1] - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 0),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            output,
            label,
            (box[0], max(16, box[1] - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return output


def summarize_tracks(rows: list[dict], config: dict) -> tuple[list[dict], dict]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[int(row["track_id"])].append(row)

    summaries = []
    for track_id, track_rows in sorted(grouped.items()):
        track_rows.sort(key=lambda item: int(item["frame_index"]))
        first = track_rows[0]
        last = track_rows[-1]
        elapsed = float(last["time"]) - float(first["time"])
        dx = float(last["x_physical"]) - float(first["x_physical"])
        dy = float(last["y_physical"]) - float(first["y_physical"])
        displacement = math.hypot(dx, dy)
        qualified = (
            len(track_rows) >= config["minimum_track_frames"]
            and displacement >= config["minimum_track_displacement"]
        )
        summaries.append(
            {
                "track_id": track_id,
                "observations": len(track_rows),
                "first_frame": int(first["frame_index"]),
                "last_frame": int(last["frame_index"]),
                "start_time": float(first["time"]),
                "end_time": float(last["time"]),
                "lifetime": elapsed,
                "start_xy": [float(first["x_physical"]), float(first["y_physical"])],
                "end_xy": [float(last["x_physical"]), float(last["y_physical"])],
                "displacement": displacement,
                "mean_velocity_xy": [dx / elapsed, dy / elapsed] if elapsed > 0 else None,
                "mean_score": sum(float(item["score"]) for item in track_rows)
                / len(track_rows),
                "maximum_score": max(float(item["score"]) for item in track_rows),
                "maximum_prompt_support": max(
                    int(item["prompt_support"]) for item in track_rows
                ),
                "mean_raster_proxy_fraction": sum(
                    float(item["raster_proxy_fraction"]) for item in track_rows
                )
                / len(track_rows),
                "qualified": qualified,
            }
        )

    qualified = [item for item in summaries if item["qualified"]]
    birth_times = sorted(item["start_time"] for item in qualified)
    intervals = [
        later - earlier for earlier, later in zip(birth_times, birth_times[1:])
        if later > earlier
    ]
    intervals.sort()
    median_interval = None
    if intervals:
        middle = len(intervals) // 2
        median_interval = (
            intervals[middle]
            if len(intervals) % 2
            else 0.5 * (intervals[middle - 1] + intervals[middle])
        )
    temporal = {
        "unique_tracks": len(summaries),
        "qualified_tracks": len(qualified),
        "qualified_track_birth_times": birth_times,
        "inter_birth_intervals": intervals,
        "median_inter_birth_interval": median_interval,
        "nondimensional_shedding_frequency_proxy": (
            1.0 / median_interval if median_interval and median_interval > 0 else None
        ),
    }
    return summaries, temporal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dart-repo", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--source-video", type=Path, default=None)
    parser.add_argument("--source-archive", type=Path, default=None)
    parser.add_argument("--source-search-root", type=Path, action="append", default=[])
    parser.add_argument("--source-cache-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("results/stage3-manual"))
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--imgsz", type=int, default=1008)
    parser.add_argument("--max-frames", type=int, default=0)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    config_path = args.config or (root / "dart_stage3.json")
    config = json.loads(config_path.read_text())
    validate_config(config)
    search_roots = args.source_search_root or [
        Path(value) for value in config.get("source_search_roots", [])
    ]
    try:
        video_path, video_resolution = resolve_source_video(
            Path(config["source_video"]),
            explicit_video=args.source_video,
            explicit_archive=args.source_archive,
            search_roots=search_roots,
            cache_dir=args.source_cache_dir,
            archive_basename=config.get("source_archive_basename"),
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    if not args.checkpoint.is_file():
        parser.error(f"SAM3 checkpoint not found: {args.checkpoint}")
    if not (args.dart_repo / "demo_multiclass.py").is_file():
        parser.error(f"DART repository not found: {args.dart_repo}")
    if args.imgsz % 14:
        parser.error("--imgsz must be divisible by 14")

    import cv2
    import numpy as np
    import torch
    from PIL import Image

    sys.path.insert(0, str(args.dart_repo.resolve()))
    from sam3.model.sam3_multiclass import Sam3MultiClassPredictor
    from sam3.model_builder import build_sam3_image_model
    from sam3.tracking import BYTETracker

    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"STAGE3_SOURCE_VIDEO={video_path}")
    print(f"STAGE3_SOURCE_RESOLUTION={video_resolution}")
    started = datetime.now(timezone.utc)

    model_started = time.perf_counter()
    model = build_sam3_image_model(
        device=args.device,
        checkpoint_path=str(args.checkpoint.resolve()),
        eval_mode=True,
    )
    predictor = Sam3MultiClassPredictor(
        model,
        device=args.device,
        resolution=args.imgsz,
        detection_only=True,
    )
    predictor.set_classes(config["prompts"])
    if args.device == "cuda":
        torch.cuda.synchronize()
    model_load_seconds = time.perf_counter() - model_started

    tracker = BYTETracker(
        track_thresh=config["track_score_threshold"],
        match_thresh=config["track_match_threshold"],
        max_time_lost=config["track_max_time_lost_frames"],
        class_agnostic_nms_thresh=1.0,
    )

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open {video_path}")
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    playback_fps = float(capture.get(cv2.CAP_PROP_FPS))
    source_frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    crop_pixels = normalized_crop_pixels(
        source_width, source_height, config["analysis_crop_normalized"]
    )
    crop_width = crop_pixels[2] - crop_pixels[0]
    crop_height = crop_pixels[3] - crop_pixels[1]
    writer = cv2.VideoWriter(
        str(output_dir / "stage3_tracked_vortices.mp4"),
        cv2.VideoWriter_fourcc(*"mp4v"),
        playback_fps if playback_fps > 0 else 2.0,
        (crop_width, crop_height),
    )

    raw_rows = []
    track_rows = []
    frame_index = 0
    inference_seconds = 0.0
    accepted_consensus_total = 0
    preview_indices = {0, max(source_frame_count // 2, 0), max(source_frame_count - 1, 0)}

    while True:
        ok, frame_bgr = capture.read()
        if not ok or (args.max_frames and frame_index >= args.max_frames):
            break
        left, top, right, bottom = crop_pixels
        crop_bgr = frame_bgr[top:bottom, left:right]
        crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)

        if args.device == "cuda":
            torch.cuda.synchronize()
        inference_started = time.perf_counter()
        state = predictor.set_image(Image.fromarray(crop_rgb))
        results = predictor.predict(
            state,
            confidence_threshold=config["score_floor"],
            nms_threshold=config["nms_iou"],
            per_class_nms=True,
        )
        if args.device == "cuda":
            torch.cuda.synchronize()
        inference_seconds += time.perf_counter() - inference_started

        detections = []
        image_area = max(crop_width * crop_height, 1)
        for score, box, prompt in zip(
            results["scores"].detach().float().cpu().tolist(),
            results["boxes"].detach().float().cpu().tolist(),
            list(results["class_names"]),
        ):
            box = [float(value) for value in box]
            area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
            detection = {
                "prompt": prompt,
                "score": float(score),
                "box_xyxy": box,
                "box_area_fraction": area / image_area,
            }
            detections.append(detection)

        area_eligible = [
            item for item in detections
            if item["box_area_fraction"] <= config["maximum_box_area_fraction"]
        ]
        clusters = cluster_detections(area_eligible, config["consensus_iou"])
        accepted = []
        for cluster_id, cluster in enumerate(clusters):
            proxy_fraction = raster_proxy_fraction(
                crop_rgb,
                cluster["box_xyxy"],
                config["raster_proxy_chroma_threshold"],
            )
            cluster["raster_proxy_fraction"] = proxy_fraction
            cluster["cluster_id"] = cluster_id
            cluster["accepted"] = (
                cluster["prompt_support"] >= config["minimum_prompt_support"]
                and proxy_fraction >= config["minimum_raster_proxy_fraction"]
                and cluster["score"] >= config["track_score_threshold"]
            )
            if cluster["accepted"]:
                accepted.append(cluster)

        accepted_consensus_total += len(accepted)
        for detection in detections:
            best_cluster = None
            best_overlap = 0.0
            for cluster in clusters:
                overlap = box_iou(detection["box_xyxy"], cluster["box_xyxy"])
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_cluster = cluster
            raw_rows.append(
                {
                    "frame_index": frame_index,
                    "time": frame_index * config["snapshot_dt"],
                    "prompt": detection["prompt"],
                    "score": detection["score"],
                    "box_area_fraction": detection["box_area_fraction"],
                    "box_x1": detection["box_xyxy"][0],
                    "box_y1": detection["box_xyxy"][1],
                    "box_x2": detection["box_xyxy"][2],
                    "box_y2": detection["box_xyxy"][3],
                    "cluster_id": best_cluster["cluster_id"] if best_cluster else "",
                    "cluster_iou": best_overlap if best_cluster else "",
                    "prompt_support": best_cluster["prompt_support"] if best_cluster else 0,
                    "consensus_score": best_cluster["score"] if best_cluster else "",
                    "raster_proxy_fraction": (
                        best_cluster["raster_proxy_fraction"] if best_cluster else ""
                    ),
                    "accepted": best_cluster["accepted"] if best_cluster else False,
                }
            )

        if accepted:
            boxes_np = np.asarray([item["box_xyxy"] for item in accepted], dtype=np.float32)
            scores_np = np.asarray([item["score"] for item in accepted], dtype=np.float32)
            class_ids_np = np.zeros(len(accepted), dtype=np.int64)
        else:
            boxes_np = np.empty((0, 4), dtype=np.float32)
            scores_np = np.empty(0, dtype=np.float32)
            class_ids_np = np.empty(0, dtype=np.int64)
        tracks = tracker.update(boxes_np, scores_np, class_ids_np)

        metadata_by_track = {}
        for track in tracks:
            best = None
            best_overlap = 0.0
            for cluster in accepted:
                overlap = box_iou(track.box_xyxy.tolist(), cluster["box_xyxy"])
                if overlap > best_overlap:
                    best_overlap = overlap
                    best = cluster
            if best is None:
                continue
            box = track.box_xyxy.tolist()
            center_crop_x = 0.5 * (box[0] + box[2])
            center_crop_y = 0.5 * (box[1] + box[3])
            center_source_x = left + center_crop_x
            center_source_y = top + center_crop_y
            x_physical, y_physical = source_to_physical(
                center_source_x,
                center_source_y,
                (source_width, source_height),
                config["plot_bounds_normalized"],
                config["physical_xlim"],
                config["physical_ylim"],
            )
            row = {
                "frame_index": frame_index,
                "time": frame_index * config["snapshot_dt"],
                "track_id": int(track.track_id),
                "score": float(track.score),
                "prompt_support": best["prompt_support"],
                "prompts": "|".join(best["prompts"]),
                "raster_proxy_fraction": best["raster_proxy_fraction"],
                "box_x1": box[0],
                "box_y1": box[1],
                "box_x2": box[2],
                "box_y2": box[3],
                "x_source": center_source_x,
                "y_source": center_source_y,
                "x_physical": x_physical,
                "y_physical": y_physical,
            }
            track_rows.append(row)
            metadata_by_track[int(track.track_id)] = best

        overlay = draw_overlay(crop_bgr, tracks, metadata_by_track, cv2)
        writer.write(overlay)
        if frame_index in preview_indices:
            cv2.imwrite(str(output_dir / f"stage3_frame_{frame_index:04d}.png"), overlay)

        del state, results
        if args.device == "cuda":
            torch.cuda.empty_cache()
        frame_index += 1

    capture.release()
    writer.release()

    detection_fields = list(raw_rows[0]) if raw_rows else [
        "frame_index", "time", "prompt", "score", "accepted"
    ]
    with (output_dir / "stage3_detections.csv").open("w", newline="") as handle:
        writer_csv = csv.DictWriter(handle, fieldnames=detection_fields)
        writer_csv.writeheader()
        writer_csv.writerows(raw_rows)

    track_fields = list(track_rows[0]) if track_rows else [
        "frame_index", "time", "track_id", "score", "prompt_support",
        "prompts", "raster_proxy_fraction", "box_x1", "box_y1", "box_x2",
        "box_y2", "x_source", "y_source", "x_physical", "y_physical"
    ]
    with (output_dir / "stage3_tracks.csv").open("w", newline="") as handle:
        writer_csv = csv.DictWriter(handle, fieldnames=track_fields)
        writer_csv.writeheader()
        writer_csv.writerows(track_rows)

    track_summaries, temporal = summarize_tracks(track_rows, config)
    temporal_pass = temporal["qualified_tracks"] >= config["minimum_qualified_tracks"]
    report = {
        "schema_version": 1,
        "status": "completed",
        "started_at_utc": started.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "project_commit": git_head(root.parents[1]),
        "dart_commit": git_head(args.dart_repo.resolve()),
        "case_id": config["case_id"],
        "source_video": str(video_path),
        "source_video_size": [source_width, source_height],
        "source_frame_count": source_frame_count,
        "frames_processed": frame_index,
        "playback_fps": playback_fps,
        "snapshot_dt": config["snapshot_dt"],
        "analysis_crop_pixels": crop_pixels,
        "analysis_crop_size": [crop_width, crop_height],
        "prompts": config["prompts"],
        "model_load_seconds": model_load_seconds,
        "inference_seconds": inference_seconds,
        "raw_detections": len(raw_rows),
        "accepted_consensus_detections": accepted_consensus_total,
        "track_observations": len(track_rows),
        "temporal_summary": temporal,
        "tracks": track_summaries,
        "gates": {
            "technical_execution": "pass",
            "semantic_consensus": "pass" if accepted_consensus_total else "fail",
            "temporal_persistence": "pass" if temporal_pass else "fail",
            "physical_validation": "not_run_raw_field_reference_required",
        },
        "claim_gate": (
            "temporal_signal_present_needs_raw_field_validation"
            if temporal_pass
            else "insufficient_temporal_signal"
        ),
        "limitations": [
            "The high-chroma raster proxy is visualization-dependent and is not a physical vortex criterion.",
            "Physical coordinates are mapped from recorded plot bounds and must be checked against raw grid coordinates.",
            "A publication claim requires raw-field labels from vorticity, swirling strength, Rortex, Q, or lambda2.",
            "The reported frequency is an inter-track-birth proxy, not yet a validated Strouhal number.",
        ],
    }
    (output_dir / "stage3_report.json").write_text(json.dumps(report, indent=2) + "\n")

    print("STAGE3_STATUS=completed")
    print(f"STAGE3_FRAMES={frame_index}")
    print(f"STAGE3_ACCEPTED_CONSENSUS={accepted_consensus_total}")
    print(f"STAGE3_QUALIFIED_TRACKS={temporal['qualified_tracks']}")
    print(f"STAGE3_CLAIM_GATE={report['claim_gate']}")
    print(f"STAGE3_REPORT={output_dir / 'stage3_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
