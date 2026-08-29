#!/usr/bin/env python3
"""Stage-2 DART domain-transfer screen for scientific CFD images."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw


COLORS = {
    "geometry": "#00b4d8",
    "shock": "#ff006e",
    "wake": "#8338ec",
    "vortex": "#fb5607",
    "shear": "#3a86ff",
    "separation": "#2a9d8f",
}


def git_head(repo: Path) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def crop_normalized(image: Image.Image, bounds: list[float]) -> tuple[Image.Image, list[int]]:
    if len(bounds) != 4:
        raise ValueError(f"crop must contain four values, got {bounds}")
    left, top, right, bottom = bounds
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError(f"invalid normalized crop {bounds}")
    width, height = image.size
    pixels = [
        max(0, min(width - 1, round(left * width))),
        max(0, min(height - 1, round(top * height))),
        max(1, min(width, round(right * width))),
        max(1, min(height, round(bottom * height))),
    ]
    if pixels[2] <= pixels[0] or pixels[3] <= pixels[1]:
        raise ValueError(f"empty pixel crop {pixels} from {bounds}")
    return image.crop(tuple(pixels)), pixels


def collect_detections(results, prompt_to_family: dict[str, str], size: tuple[int, int]):
    scores = results["scores"].detach().float().cpu().tolist()
    boxes = results["boxes"].detach().float().cpu().tolist()
    class_names = list(results["class_names"])
    width, height = size
    image_area = max(width * height, 1)
    detections = []
    for score, box, prompt in zip(scores, boxes, class_names):
        x1, y1, x2, y2 = [float(value) for value in box]
        box_area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        detections.append(
            {
                "prompt": prompt,
                "family": prompt_to_family[prompt],
                "score": float(score),
                "box_xyxy": [x1, y1, x2, y2],
                "box_area_fraction": box_area / image_area,
            }
        )
    detections.sort(key=lambda item: item["score"], reverse=True)
    return detections


def threshold_counts(detections, thresholds):
    output = {}
    for threshold in thresholds:
        by_family = defaultdict(int)
        kept = 0
        for detection in detections:
            if detection["score"] >= threshold:
                kept += 1
                by_family[detection["family"]] += 1
        output[f"{threshold:g}"] = {
            "total": kept,
            "by_family": dict(sorted(by_family.items())),
        }
    return output


def top_by_prompt(detections, prompts):
    best = {}
    for detection in detections:
        best.setdefault(detection["prompt"], detection)
    return {prompt: best.get(prompt) for prompt in prompts}


def top_by_family(detections, families):
    best = {}
    for detection in detections:
        best.setdefault(detection["family"], detection)
    return {family: best.get(family) for family in families}


def annotate_boxes(image, detections, threshold, limit):
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    selected = [item for item in detections if item["score"] >= threshold][:limit]
    for detection in reversed(selected):
        x1, y1, x2, y2 = detection["box_xyxy"]
        family = detection["family"]
        color = COLORS.get(family, "#ffffff")
        draw.rectangle((x1, y1, x2, y2), outline=color, width=3)
        label = f'{family}:{detection["prompt"]} {detection["score"]:.3f}'
        text_y = max(0, y1 - 14)
        draw.rectangle((x1, text_y, min(image.width, x1 + 9 * len(label)), text_y + 14), fill=color)
        draw.text((x1 + 2, text_y + 1), label, fill="#000000")
    return annotated, len(selected)


def validate_config(config, base_cases):
    thresholds = config["report_thresholds"]
    if thresholds != sorted(thresholds) or config["score_floor"] != thresholds[0]:
        raise ValueError("report_thresholds must be sorted and start at score_floor")
    prompt_families = config["prompt_families"]
    prompts = [prompt for values in prompt_families.values() for prompt in values]
    if len(prompts) != len(set(prompts)):
        raise ValueError("prompt strings must be unique across families")
    config_ids = {case["id"] for case in config["cases"]}
    if config_ids != set(base_cases):
        raise ValueError("stage-2 cases must exactly match dart_cases.json")
    for case in config["cases"]:
        for family in case["families"]:
            if family not in prompt_families:
                raise ValueError(f"unknown prompt family {family}")
        for name, bounds in case["views"].items():
            if name not in {"plot", "body", "wake"}:
                raise ValueError(f"unsupported view {name}")
            crop_normalized(Image.new("RGB", (100, 100)), bounds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dart-repo", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--imgsz", type=int, default=1008)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("results/stage2-manual"))
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    dart_repo = args.dart_repo.resolve()
    checkpoint = args.checkpoint.resolve()
    config_path = args.config or (root / "dart_stage2.json")
    config = json.loads(config_path.read_text())
    base_config = json.loads((root / "dart_cases.json").read_text())
    base_cases = {case["id"]: case for case in base_config["cases"]}
    validate_config(config, base_cases)

    if not checkpoint.is_file():
        parser.error(f"SAM3 checkpoint not found: {checkpoint}")
    if not (dart_repo / "demo_multiclass.py").is_file():
        parser.error(f"DART repository not found: {dart_repo}")
    if args.imgsz % 14:
        parser.error("--imgsz must be divisible by 14")

    sys.path.insert(0, str(dart_repo))
    import torch
    from sam3.model.sam3_multiclass import Sam3MultiClassPredictor
    from sam3.model_builder import build_sam3_image_model

    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc)
    load_started = time.perf_counter()
    model = build_sam3_image_model(
        device=args.device,
        checkpoint_path=str(checkpoint),
        eval_mode=True,
    )
    predictor = Sam3MultiClassPredictor(
        model,
        device=args.device,
        resolution=args.imgsz,
        detection_only=True,
    )
    if args.device == "cuda":
        torch.cuda.synchronize()
    model_load_seconds = time.perf_counter() - load_started

    prompt_families = config["prompt_families"]
    prompt_to_family = {
        prompt: family
        for family, prompts in prompt_families.items()
        for prompt in prompts
    }
    report = {
        "schema_version": 1,
        "started_at_utc": started.isoformat(),
        "project_commit": git_head(root.parents[1]),
        "dart_commit": git_head(dart_repo),
        "checkpoint": str(checkpoint),
        "device": args.device,
        "imgsz": args.imgsz,
        "score_floor": config["score_floor"],
        "report_thresholds": config["report_thresholds"],
        "model_load_seconds": model_load_seconds,
        "views": [],
        "interpretation_gate": {
            "status": "diagnostic_only",
            "reason": (
                "Prompt consistency and scores can screen domain transfer, but physical "
                "acceptance requires CFD-derived reference masks."
            ),
        },
    }
    csv_rows = []

    for case_spec in config["cases"]:
        case_id = case_spec["id"]
        base_case = base_cases[case_id]
        image_path = (root / base_case["image"]).resolve()
        source = Image.open(image_path).convert("RGB")
        families = case_spec["families"]
        prompts = [
            prompt
            for family in families
            for prompt in prompt_families[family]
        ]
        predictor.set_classes(prompts)

        for view_name, bounds in case_spec["views"].items():
            cropped, crop_pixels = crop_normalized(source, bounds)
            stem = f"{case_id}__{view_name}"
            crop_path = output_dir / f"{stem}__crop.png"
            boxes_path = output_dir / f"{stem}__boxes.png"
            cropped.save(crop_path)

            if args.device == "cuda":
                torch.cuda.synchronize()
            inference_started = time.perf_counter()
            state = predictor.set_image(cropped)
            results = predictor.predict(
                state,
                confidence_threshold=config["score_floor"],
                nms_threshold=0.7,
                per_class_nms=True,
            )
            if args.device == "cuda":
                torch.cuda.synchronize()
            inference_seconds = time.perf_counter() - inference_started

            detections = collect_detections(results, prompt_to_family, cropped.size)
            prompt_best = top_by_prompt(detections, prompts)
            family_best = top_by_family(detections, families)
            annotated, annotations_drawn = annotate_boxes(
                cropped,
                detections,
                config["annotate_threshold"],
                config["max_annotations"],
            )
            annotated.save(boxes_path)

            for prompt in prompts:
                detection = prompt_best[prompt]
                row = {
                    "case_id": case_id,
                    "view": view_name,
                    "family": prompt_to_family[prompt],
                    "prompt": prompt,
                    "top_score": "",
                    "box_x1": "",
                    "box_y1": "",
                    "box_x2": "",
                    "box_y2": "",
                    "box_area_fraction": "",
                }
                if detection is not None:
                    row["top_score"] = f'{detection["score"]:.8f}'
                    row["box_area_fraction"] = f'{detection["box_area_fraction"]:.8f}'
                    for key, value in zip(
                        ["box_x1", "box_y1", "box_x2", "box_y2"],
                        detection["box_xyxy"],
                    ):
                        row[key] = f"{value:.4f}"
                csv_rows.append(row)

            report["views"].append(
                {
                    "case_id": case_id,
                    "field": base_case["field"],
                    "view": view_name,
                    "source_image": display_path(image_path, root),
                    "source_size": list(source.size),
                    "normalized_crop": bounds,
                    "pixel_crop": crop_pixels,
                    "crop_size": list(cropped.size),
                    "families": families,
                    "prompts": prompts,
                    "inference_seconds": inference_seconds,
                    "detections_at_score_floor": len(detections),
                    "threshold_counts": threshold_counts(
                        detections, config["report_thresholds"]
                    ),
                    "top_by_prompt": prompt_best,
                    "top_by_family": family_best,
                    "top_detections": detections[:100],
                    "crop_image": display_path(crop_path, root),
                    "box_image": display_path(boxes_path, root),
                    "annotations_drawn": annotations_drawn,
                }
            )
            del state, results
            if args.device == "cuda":
                torch.cuda.empty_cache()

    fieldnames = [
        "case_id",
        "view",
        "family",
        "prompt",
        "top_score",
        "box_x1",
        "box_y1",
        "box_x2",
        "box_y2",
        "box_area_fraction",
    ]
    csv_path = output_dir / "stage2_prompt_scores.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    top_scores = [
        detection["score"]
        for view in report["views"]
        for detection in view["top_by_prompt"].values()
        if detection is not None
    ]
    flow_scores = [
        detection["score"]
        for view in report["views"]
        for family, detection in view["top_by_family"].items()
        if family != "geometry" and detection is not None
    ]
    geometry_scores = [
        detection["score"]
        for view in report["views"]
        for family, detection in view["top_by_family"].items()
        if family == "geometry" and detection is not None
    ]
    report["screening_summary"] = {
        "maximum_prompt_score": max(top_scores, default=None),
        "maximum_geometry_score": max(geometry_scores, default=None),
        "maximum_flow_structure_score": max(flow_scores, default=None),
        "prompt_view_pairs_at_or_above_0_10": sum(
            score >= 0.10 for score in top_scores
        ),
        "prompt_view_pairs_at_or_above_0_15": sum(
            score >= 0.15 for score in top_scores
        ),
        "domain_transfer_signal": (
            "present_but_not_physically_validated"
            if sum(score >= 0.10 for score in flow_scores) >= 2
            else "weak_or_absent"
        ),
    }
    report["status"] = "completed"
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    report_path = output_dir / "stage2_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")

    print(f"STAGE2_STATUS={report['status']}")
    print(f"STAGE2_SIGNAL={report['screening_summary']['domain_transfer_signal']}")
    print(f"STAGE2_REPORT={report_path}")
    print(f"STAGE2_SCORES={csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
