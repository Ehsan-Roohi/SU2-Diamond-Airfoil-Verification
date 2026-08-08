#!/usr/bin/env python3
"""Normalize Nektar++ AeroForces output and estimate correlated uncertainty."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path


SU2_SST = {
    0.0: {'CL': 0.000156, 'CD': 0.025260, 'CDp': 0.020198, 'CDv': 0.005062},
    4.0: {'CL': 0.084907, 'CD': 0.032106, 'CDp': 0.026488, 'CDv': 0.005617},
    8.0: {'CL': 0.169830, 'CD': 0.054872, 'CDp': 0.044410, 'CDv': 0.010462},
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument('force_file')
    p.add_argument('--alpha', type=float, choices=(0.0, 4.0, 8.0), required=True)
    p.add_argument('--span', type=float, required=True)
    p.add_argument('--window', type=float, required=True)
    p.add_argument('--rho', type=float, default=1.0)
    p.add_argument('--speed', type=float, default=1.0)
    p.add_argument('--chord', type=float, default=1.0)
    p.add_argument('--output-dir')
    return p.parse_args()


def read_force_file(path: Path) -> list[list[float]]:
    rows = []
    for raw in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        try:
            values = [float(x) for x in line.split()]
        except ValueError:
            continue
        # 3-D output: time, 3 force triples, 3 moment triples = 19 columns.
        if len(values) >= 10 and all(math.isfinite(x) for x in values[:10]):
            rows.append(values)
    if not rows:
        raise SystemExit(f'no valid force records in {path}')
    return rows


def correlated_stats(values: list[float]) -> dict[str, float]:
    n = len(values)
    mean = statistics.fmean(values)
    std = statistics.stdev(values) if n > 1 else 0.0
    if n < 4 or std == 0.0:
        tau, neff = 1.0, float(n)
    else:
        var = statistics.fmean([(v - mean) ** 2 for v in values])
        rho_sum = 0.0
        for lag in range(1, max(2, min(n // 4, 2000))):
            cov = statistics.fmean(
                [(values[i] - mean) * (values[i + lag] - mean) for i in range(n - lag)]
            )
            rho_lag = cov / var
            if rho_lag <= 0.0:
                break
            rho_sum += rho_lag
        tau = max(1.0, 1.0 + 2.0 * rho_sum)
        neff = max(1.0, n / tau)
    ci95 = 1.96 * std / math.sqrt(neff) if neff > 0 else float('nan')
    return {'mean': mean, 'std': std, 'tau_int_samples': tau, 'n_eff': neff, 'ci95': ci95}


def percent_delta(value: float, reference: float) -> float | None:
    if reference == 0.0:
        return None
    return 100.0 * (value - reference) / reference


def main() -> None:
    a = parse_args()
    force_path = Path(a.force_file)
    rows = read_force_file(force_path)
    out_dir = Path(a.output_dir) if a.output_dir else force_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    q_area = 0.5 * a.rho * a.speed**2 * a.chord * a.span
    ca = math.cos(math.radians(a.alpha))
    sa = math.sin(math.radians(a.alpha))

    history = []
    for r in rows:
        t = r[0]
        fxp, fxv = r[1], r[2]
        fyp, fyv = r[4], r[5]
        cdp = (fxp * ca + fyp * sa) / q_area
        clp = (fyp * ca - fxp * sa) / q_area
        cdv = (fxv * ca + fyv * sa) / q_area
        clv = (fyv * ca - fxv * sa) / q_area
        history.append(
            {
                'time': t,
                'CL': clp + clv,
                'CD': cdp + cdv,
                'CLp': clp,
                'CLv': clv,
                'CDp': cdp,
                'CDv': cdv,
            }
        )

    end_time = history[-1]['time']
    selected = [h for h in history if h['time'] >= end_time - a.window]
    if len(selected) < 10:
        raise SystemExit('fewer than 10 force samples in requested averaging window')

    csv_path = out_dir / 'force_coefficients.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(history[0]))
        writer.writeheader()
        writer.writerows(history)

    stats = {key: correlated_stats([h[key] for h in selected]) for key in ('CL', 'CD', 'CDp', 'CDv')}
    reference = SU2_SST[a.alpha]
    comparison = {
        key: {
            'ILES_mean': stats[key]['mean'],
            'SU2_SST': reference[key],
            'percent_delta': percent_delta(stats[key]['mean'], reference[key]),
        }
        for key in ('CL', 'CD', 'CDp', 'CDv')
    }

    half = len(selected) // 2
    first_cd = statistics.fmean(h['CD'] for h in selected[:half])
    second_cd = statistics.fmean(h['CD'] for h in selected[half:])
    drift_scale = max(abs(stats['CD']['mean']), 0.01)
    drift_fraction = abs(second_cd - first_cd) / drift_scale
    checks = {
        'enough_total_records': len(history) >= 20,
        'enough_window_records': len(selected) >= 10,
        'finite_coefficients': all(math.isfinite(h[k]) for h in selected for k in ('CL', 'CD')),
        'positive_mean_drag': stats['CD']['mean'] > 0.0,
        'no_large_CD_drift': drift_fraction < 0.10,
    }
    passed = all(checks.values())
    summary = {
        'status': 'PASS' if passed else 'FAIL',
        'alpha_deg': a.alpha,
        'span_over_chord': a.span / a.chord,
        'records_total': len(history),
        'records_in_window': len(selected),
        'window': [selected[0]['time'], selected[-1]['time']],
        'statistics': stats,
        'CD_half_window_drift_fraction': drift_fraction,
        'checks': checks,
        'comparison_to_SU2_SST': comparison,
        'model_note': 'ILES and fully-turbulent two-dimensional SST are different closures; disagreement is diagnostic, not automatically an error.',
    }
    (out_dir / 'force_summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    verdict = [summary['status']]
    verdict.extend(f'{name}={"PASS" if ok else "FAIL"}' for name, ok in checks.items())
    verdict.append(f'CL_mean={stats["CL"]["mean"]:.8g} +/- {stats["CL"]["ci95"]:.3g} (95% correlated CI)')
    verdict.append(f'CD_mean={stats["CD"]["mean"]:.8g} +/- {stats["CD"]["ci95"]:.3g} (95% correlated CI)')
    (out_dir / 'PASS_FAIL.txt').write_text('\n'.join(verdict) + '\n', encoding='utf-8')
    print('\n'.join(verdict))


if __name__ == '__main__':
    main()
