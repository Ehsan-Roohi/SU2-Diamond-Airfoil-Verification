#!/usr/bin/env python3
"""Generate exact perfect-gas normal-shock references for the cylinder nose."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def normal_shock(mach: float, gamma: float = 1.4) -> dict[str, float]:
    if not math.isfinite(mach) or mach <= 1.0:
        raise ValueError("Mach number must be finite and greater than one")
    if not math.isfinite(gamma) or gamma <= 1.0:
        raise ValueError("gamma must be finite and greater than one")

    density_ratio = ((gamma + 1.0) * mach**2) / (
        (gamma - 1.0) * mach**2 + 2.0
    )
    pressure_ratio = 1.0 + 2.0 * gamma * (mach**2 - 1.0) / (gamma + 1.0)
    temperature_ratio = pressure_ratio / density_ratio
    downstream_mach_sq = (
        1.0 + 0.5 * (gamma - 1.0) * mach**2
    ) / (gamma * mach**2 - 0.5 * (gamma - 1.0))
    downstream_mach = math.sqrt(downstream_mach_sq)
    p02_over_p1 = pressure_ratio * (
        1.0 + 0.5 * (gamma - 1.0) * downstream_mach_sq
    ) ** (gamma / (gamma - 1.0))
    cp_stagnation = (p02_over_p1 - 1.0) / (0.5 * gamma * mach**2)
    return {
        "mach_upstream": mach,
        "gamma": gamma,
        "rho2_over_rho1": density_ratio,
        "p2_over_p1": pressure_ratio,
        "T2_over_T1": temperature_ratio,
        "mach_downstream": downstream_mach,
        "p02_over_p1": p02_over_p1,
        "inviscid_stagnation_cp": cp_stagnation,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mach", type=float, default=2.7)
    parser.add_argument("--gamma", type=float, default=1.4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = normal_shock(args.mach, args.gamma)
    except ValueError as exc:
        parser.error(str(exc))
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        args.output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
