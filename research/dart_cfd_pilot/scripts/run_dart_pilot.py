#!/usr/bin/env python3
"""Run DART on MFC Euler, MFC viscous, and SU2/SST control images."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def git_head(repo: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dart-repo", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--imgsz", type=int, default=1008)
    parser.add_argument("--confidence", type=float, default=0.15)
    parser.add_argument("--detection-only", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("results/manual"))
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "dart_cases.json").read_text())
    demo = args.dart_repo.resolve() / "demo_multiclass.py"
    checkpoint = args.checkpoint.resolve()
    if not demo.is_file():
        parser.error(f"DART demo not found: {demo}")
    if not checkpoint.is_file():
        parser.error(f"SAM3 checkpoint not found: {checkpoint}")
    if args.imgsz % 14:
        parser.error("--imgsz must be divisible by 14")

    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "dart_commit": git_head(args.dart_repo.resolve()),
        "checkpoint": str(checkpoint),
        "device": args.device,
        "imgsz": args.imgsz,
        "confidence": args.confidence,
        "detection_only": args.detection_only,
        "runs": [],
    }

    for case in config["cases"]:
        image = (root / case["image"]).resolve()
        output = output_dir / f"{case['id']}_dart.png"
        prompts = case.get("prompts", config["prompts"])
        command = [
            args.python,
            str(demo),
            "--image",
            str(image),
            "--classes",
            *prompts,
            "--checkpoint",
            str(checkpoint),
            "--device",
            args.device,
            "--imgsz",
            str(args.imgsz),
            "--confidence",
            str(args.confidence),
            "--output",
            str(output),
        ]
        if args.detection_only:
            command.insert(-2, "--detection-only")
        completed = subprocess.run(
            command,
            cwd=args.dart_repo,
            text=True,
            capture_output=True,
        )
        report["runs"].append(
            {
                "case_id": case["id"],
                "prompts": prompts,
                "returncode": completed.returncode,
                "output": display_path(output, root) if output.exists() else None,
                "stdout_tail": completed.stdout[-4000:],
                "stderr_tail": completed.stderr[-4000:],
            }
        )

    report["status"] = (
        "completed" if all(run["returncode"] == 0 for run in report["runs"]) else "failed"
    )
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    report_path = output_dir / "dart_run_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(report_path)
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
