#!/usr/bin/env python3
"""Render a Nektar++ session from the pinned template."""

from __future__ import annotations

import argparse
from pathlib import Path
from xml.sax.saxutils import escape


def args_parser() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument('--template', default='templates/session.xml.in')
    p.add_argument('--output', required=True)
    p.add_argument('--alpha', type=float, choices=(0.0, 4.0, 8.0), required=True)
    p.add_argument('--order', type=int, choices=(2, 3, 4), required=True)
    p.add_argument('--lz', type=float, required=True)
    p.add_argument('--dt', type=float, required=True)
    p.add_argument('--steps', type=int, required=True)
    p.add_argument('--time', type=float, default=0.0)
    p.add_argument('--restart')
    p.add_argument('--pert-amp', type=float, default=0.002)
    p.add_argument('--av-mu0', type=float, default=0.5)
    p.add_argument('--force-file', required=True)
    p.add_argument('--force-frequency', type=int, default=10)
    p.add_argument('--force-start', type=float, default=0.0)
    p.add_argument('--check-file', required=True)
    p.add_argument('--check-frequency', type=int, required=True)
    p.add_argument('--info-frequency', type=int, default=100)
    return p.parse_args()


def main() -> None:
    a = args_parser()
    template = Path(a.template).read_text(encoding='utf-8')
    if a.restart:
        initial = (
            f'            <F VAR="rho,rhou,rhov,rhow,E" '
            f'FILE="{escape(str(Path(a.restart).resolve()))}" />'
        )
    else:
        initial = '\n'.join(
            [
                '            <E VAR="rho"  VALUE="rhoInf" />',
                '            <E VAR="rhou" VALUE="rhoInf*uInf" />',
                '            <E VAR="rhov" VALUE="rhoInf*vInf" />',
                '            <E VAR="rhow" VALUE="rhoInf*wSeed" />',
                '            <E VAR="E" VALUE="EInf + 0.5*rhoInf*wSeed*wSeed" />',
            ]
        )
    replacements = {
        '@ORDER@': str(a.order),
        '@TIME@': f'{a.time:.16g}',
        '@DT@': f'{a.dt:.16g}',
        '@NSTEPS@': str(a.steps),
        '@INFO_FREQ@': str(a.info_frequency),
        '@ALPHA@': f'{a.alpha:.16g}',
        '@LZ@': f'{a.lz:.16g}',
        '@PERT_AMP@': f'{a.pert_amp:.16g}',
        '@AV_MU0@': f'{a.av_mu0:.16g}',
        '@INITIAL_CONDITIONS@': initial,
        '@FORCE_FILE@': escape(a.force_file),
        '@FORCE_FREQ@': str(a.force_frequency),
        '@FORCE_START@': f'{a.force_start:.16g}',
        '@CHECK_FILE@': escape(a.check_file),
        '@CHECK_FREQ@': str(a.check_frequency),
    }
    rendered = template
    for old, new in replacements.items():
        rendered = rendered.replace(old, new)
    leftovers = sorted({word for word in rendered.split() if '@' in word})
    if leftovers:
        raise SystemExit(f'unresolved template tokens: {leftovers}')
    output = Path(a.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
