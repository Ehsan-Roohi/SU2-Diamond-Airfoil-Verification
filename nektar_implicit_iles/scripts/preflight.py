#!/usr/bin/env python3
"""Structural checks for generated Gmsh geometry, Nektar mesh, and session."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument('--geo')
    p.add_argument('--mesh')
    p.add_argument('--session')
    p.add_argument('--check-programs', action='store_true')
    return p.parse_args()


def program_checks() -> list[str]:
    messages = []
    for program in ('gmsh', 'NekMesh', 'CompressibleFlowSolver', 'python3'):
        path = shutil.which(program)
        if not path:
            raise SystemExit(f'PRECHECK FAIL: missing executable: {program}')
        messages.append(f'program {program}: {path}')
    for program in ('gmsh', 'NekMesh', 'CompressibleFlowSolver'):
        try:
            result = subprocess.run(
                [program, '--version'], text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=15, check=False
            )
            first = result.stdout.strip().splitlines()[0] if result.stdout.strip() else 'version not printed'
            messages.append(f'version {program}: {first}')
        except (OSError, subprocess.TimeoutExpired):
            messages.append(f'version {program}: unavailable, executable check passed')
    return messages


def geo_checks(path: Path) -> list[str]:
    text = path.read_text(encoding='utf-8')
    required = (
        'Physical Curve(1)', 'Physical Curve(2)', 'Physical Surface(100)',
        'BoundaryLayer Field = 3', 'Plane Surface(100)',
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit(f'PRECHECK FAIL: missing Gmsh tokens: {missing}')
    if text.count('{') != text.count('}'):
        raise SystemExit('PRECHECK FAIL: unbalanced braces in Gmsh file')
    return [f'geo structure: PASS ({path})']


def mesh_checks(path: Path) -> list[str]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise SystemExit(f'PRECHECK FAIL: mesh XML parse error: {exc}') from exc
    geometry = root.find('.//GEOMETRY')
    if geometry is None:
        raise SystemExit('PRECHECK FAIL: no GEOMETRY in mesh')
    if geometry.attrib.get('DIM') != '3' or geometry.attrib.get('SPACE') != '3':
        raise SystemExit(f'PRECHECK FAIL: expected DIM=3 SPACE=3, got {geometry.attrib}')
    comp_ids = {int(c.attrib['ID']) for c in root.findall('.//COMPOSITE/C') if 'ID' in c.attrib}
    required = {1, 2, 100, 102, 103}
    missing = required - comp_ids
    if missing:
        raise SystemExit(
            f'PRECHECK FAIL: missing composites {sorted(missing)}; found sample {sorted(comp_ids)[:20]}'
        )
    domain_text = ' '.join((d.text or '') for d in root.findall('.//DOMAIN/D'))
    if not re.search(r'C\[100\]', domain_text):
        raise SystemExit(f'PRECHECK FAIL: C[100] is not the volume domain: {domain_text!r}')
    return [f'mesh XML: PASS ({path})', f'composites: {sorted(required)} present']


def session_checks(path: Path) -> list[str]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise SystemExit(f'PRECHECK FAIL: session XML parse error: {exc}') from exc
    text = path.read_text(encoding='utf-8')
    if '@' in text:
        raise SystemExit('PRECHECK FAIL: unresolved @TOKEN@ in session')
    required = (
        'NavierStokesImplicitCFE', 'InteriorPenalty', 'Dilatation',
        'DucrosSensor', 'WallAdiabatic', 'RiemannInvariant',
        'VAR="rhow"', 'C[102]', 'C[103]', 'TYPE="AeroForces"',
        'TYPE="Checkpoint"',
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit(f'PRECHECK FAIL: missing session tokens: {missing}')
    variables = [v.text.strip() for v in root.findall('.//VARIABLES/V')]
    if variables != ['rho', 'rhou', 'rhov', 'rhow', 'E']:
        raise SystemExit(f'PRECHECK FAIL: wrong variable ordering: {variables}')
    return [f'session XML: PASS ({path})', 'implicit 3-D CFS configuration: PASS']


def main() -> None:
    a = parse_args()
    messages = []
    if a.check_programs:
        messages.extend(program_checks())
    if a.geo:
        messages.extend(geo_checks(Path(a.geo)))
    if a.mesh:
        messages.extend(mesh_checks(Path(a.mesh)))
    if a.session:
        messages.extend(session_checks(Path(a.session)))
    if not messages:
        raise SystemExit('nothing to check')
    print('\n'.join(messages))
    print('PRECHECK PASS')


if __name__ == '__main__':
    main()

