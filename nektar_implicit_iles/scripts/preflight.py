#!/usr/bin/env python3
"""Structural checks for generated Gmsh geometry, Nektar mesh, and session."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


def ordered_entity_refs(text: str, entity: str) -> list[int]:
    """Expand ordered Nektar references such as F[1,3-5]."""
    result: list[int] = []
    for expression in re.findall(rf'{re.escape(entity)}\[([^\]]+)\]', text):
        for item in expression.split(','):
            item = item.strip()
            if re.fullmatch(r'\d+', item):
                result.append(int(item))
                continue
            match = re.fullmatch(r'(\d+)\s*-\s*(\d+)', item)
            if match:
                start, stop = map(int, match.groups())
                if stop < start:
                    raise ValueError(f'descending composite range: {item}')
                result.extend(range(start, stop + 1))
    return result


def composite_refs(text: str) -> set[int]:
    """Expand Nektar composite references such as C[1,3-5]."""
    return set(ordered_entity_refs(text, 'C'))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument('--geo')
    p.add_argument('--mesh')
    p.add_argument('--session')
    p.add_argument('--align-periodic')
    p.add_argument('--periodic-surf1', type=int, default=103)
    p.add_argument('--periodic-surf2', type=int, default=104)
    p.add_argument('--periodic-dir', choices=('x', 'y', 'z'), default='z')
    p.add_argument('--periodic-tolerance', type=float, default=1.0e-9)
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
        except subprocess.TimeoutExpired as exc:
            raise SystemExit(
                f'PRECHECK FAIL: {program} --version timed out after 15 seconds'
            ) from exc
        except OSError as exc:
            raise SystemExit(
                f'PRECHECK FAIL: could not launch {program}: {exc}'
            ) from exc
        output = result.stdout.strip()
        first = output.splitlines()[0] if output else 'version not printed'
        if result.returncode != 0:
            raise SystemExit(
                f'PRECHECK FAIL: {program} --version exited '
                f'{result.returncode}: {first}'
            )
        messages.append(f'version {program}: {first}')
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


def align_periodic_faces(
    path: Path,
    surf1: int = 103,
    surf2: int = 104,
    direction: str = 'z',
    tolerance: float = 1.0e-9,
) -> list[str]:
    """Pair extruded periodic faces geometrically without linearising the mesh."""
    if tolerance <= 0:
        raise SystemExit('PRECHECK FAIL: periodic tolerance must be positive')
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise SystemExit(f'PRECHECK FAIL: mesh XML parse error: {exc}') from exc

    for section_name in ('VERTEX', 'EDGE', 'FACE'):
        section = root.find(f'.//{section_name}')
        if section is None:
            raise SystemExit(f'PRECHECK FAIL: no {section_name} in periodic mesh')
        if section.attrib.get('COMPRESSED'):
            raise SystemExit(
                'PRECHECK FAIL: periodic alignment requires uncompressed XML; '
                'write the NekMesh output as mesh3d.xml:xml:uncompress'
            )

    try:
        vertices = {
            int(vertex.attrib['ID']): tuple(map(float, (vertex.text or '').split()[:3]))
            for vertex in root.findall('.//VERTEX/V')
        }
        edges = {
            int(edge.attrib['ID']): tuple(map(int, (edge.text or '').split()[:2]))
            for edge in root.findall('.//EDGE/E')
        }
    except (KeyError, ValueError) as exc:
        raise SystemExit(f'PRECHECK FAIL: malformed periodic mesh entities: {exc}') from exc
    if not vertices or not edges:
        raise SystemExit('PRECHECK FAIL: empty VERTEX or EDGE block in periodic mesh')

    faces: dict[int, tuple[str, tuple[int, ...]]] = {}
    for face in root.findall('.//FACE/*'):
        try:
            face_id = int(face.attrib['ID'])
            edge_ids = [int(value) for value in (face.text or '').split()]
            vertex_ids = sorted({vertex for edge_id in edge_ids for vertex in edges[edge_id]})
        except (KeyError, ValueError) as exc:
            raise SystemExit(f'PRECHECK FAIL: malformed face connectivity: {exc}') from exc
        faces[face_id] = (face.tag, tuple(vertex_ids))

    composites = {
        int(comp.attrib['ID']): comp
        for comp in root.findall('.//COMPOSITE/C')
        if 'ID' in comp.attrib
    }
    if surf1 not in composites or surf2 not in composites:
        raise SystemExit(
            f'PRECHECK FAIL: periodic composites {surf1}/{surf2} are not both present'
        )
    refs1 = ordered_entity_refs(composites[surf1].text or '', 'F')
    refs2 = ordered_entity_refs(composites[surf2].text or '', 'F')
    if not refs1 or len(refs1) != len(refs2):
        raise SystemExit(
            'PRECHECK FAIL: periodic composites must contain the same nonzero number of faces; '
            f'got {len(refs1)} and {len(refs2)}'
        )

    axis = {'x': 0, 'y': 1, 'z': 2}[direction]
    projected_axes = [index for index in range(3) if index != axis]
    coordinate_scale = max(1.0, max(abs(value) for xyz in vertices.values() for value in xyz))
    absolute_tolerance = tolerance * coordinate_scale

    def quantize(value: float) -> int:
        return round(value / absolute_tolerance)

    def signature(face_id: int) -> tuple[str, tuple[tuple[int, int], ...]]:
        if face_id not in faces:
            raise SystemExit(f'PRECHECK FAIL: composite references missing face F[{face_id}]')
        face_type, vertex_ids = faces[face_id]
        return (
            face_type,
            tuple(sorted(
                tuple(quantize(vertices[vertex_id][index]) for index in projected_axes)
                for vertex_id in vertex_ids
            )),
        )

    def surface_plane(face_ids: list[int], surface: int) -> float:
        coordinates = []
        for face_id in face_ids:
            if face_id not in faces:
                raise SystemExit(f'PRECHECK FAIL: composite references missing face F[{face_id}]')
            face_coordinates = [vertices[v][axis] for v in faces[face_id][1]]
            if max(face_coordinates) - min(face_coordinates) > absolute_tolerance:
                raise SystemExit(
                    f'PRECHECK FAIL: face F[{face_id}] on C[{surface}] is not normal to {direction}'
                )
            coordinates.extend(face_coordinates)
        if max(coordinates) - min(coordinates) > absolute_tolerance:
            raise SystemExit(
                f'PRECHECK FAIL: composite C[{surface}] is not a single {direction}-plane'
            )
        return sum(coordinates) / len(coordinates)

    plane1 = surface_plane(refs1, surf1)
    plane2 = surface_plane(refs2, surf2)
    if abs(plane2 - plane1) <= absolute_tolerance:
        raise SystemExit('PRECHECK FAIL: periodic surfaces have zero separation')

    surface2_by_signature: dict[tuple[str, tuple[tuple[int, int], ...]], int] = {}
    for face_id in refs2:
        key = signature(face_id)
        if key in surface2_by_signature:
            raise SystemExit(
                f'PRECHECK FAIL: duplicate projected face geometry on C[{surf2}]'
            )
        surface2_by_signature[key] = face_id

    try:
        aligned_refs2 = [surface2_by_signature[signature(face_id)] for face_id in refs1]
    except KeyError as exc:
        raise SystemExit(
            f'PRECHECK FAIL: no translated partner found between C[{surf1}] and C[{surf2}]'
        ) from exc
    if set(aligned_refs2) != set(refs2):
        raise SystemExit('PRECHECK FAIL: periodic face pairing is not one-to-one')

    reordered = sum(left != right for left, right in zip(refs2, aligned_refs2))
    if reordered:
        text = path.read_text(encoding='utf-8')
        pattern = re.compile(
            rf'(<C\b(?=[^>]*\bID\s*=\s*["\']{surf2}["\'])[^>]*>).*?(</C>)',
            re.DOTALL,
        )
        replacement = rf'\1 F[{",".join(map(str, aligned_refs2))}] \2'
        updated, count = pattern.subn(replacement, text, count=1)
        if count != 1:
            raise SystemExit(f'PRECHECK FAIL: could not rewrite composite C[{surf2}]')
        temporary = path.with_suffix(path.suffix + '.periodic.tmp')
        temporary.write_text(updated, encoding='utf-8')
        temporary.replace(path)

    return [
        f'periodic faces: PASS (C[{surf1}] <-> C[{surf2}], '
        f'{len(refs1)} pairs, reordered={reordered}, delta_{direction}={plane2-plane1:.8g})'
    ]


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
    required = {1, 2, 100, 101, 103, 104}
    missing = required - comp_ids
    if missing:
        raise SystemExit(
            f'PRECHECK FAIL: missing composites {sorted(missing)}; found sample {sorted(comp_ids)[:20]}'
        )
    domain_text = ' '.join((d.text or '') for d in root.findall('.//DOMAIN/D'))
    domain_composites = composite_refs(domain_text)
    volume_composites = {100, 101}
    if not volume_composites.issubset(domain_composites):
        raise SystemExit(
            'PRECHECK FAIL: mixed prism/hex volume composites are not both in the domain: '
            f'expected {sorted(volume_composites)}, got {sorted(domain_composites)} '
            f'from {domain_text!r}'
        )
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
        'VAR="rhow"', 'C[100]', 'C[101]', 'C[103]', 'C[104]', 'TYPE="AeroForces"',
        'TYPE="Checkpoint"',
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit(f'PRECHECK FAIL: missing session tokens: {missing}')
    variables = [v.text.strip() for v in root.findall('.//VARIABLES/V')]
    if variables != ['rho', 'rhou', 'rhov', 'rhow', 'E']:
        raise SystemExit(f'PRECHECK FAIL: wrong variable ordering: {variables}')
    expansion_composites = set()
    for expansion in root.findall('.//EXPANSIONS/E'):
        expansion_composites.update(composite_refs(expansion.attrib.get('COMPOSITE', '')))
    if not {100, 101}.issubset(expansion_composites):
        raise SystemExit(
            'PRECHECK FAIL: expansions must cover both prism and hex volumes; '
            f'got {sorted(expansion_composites)}'
        )
    boundary_regions = {
        int(boundary.attrib['ID']): composite_refs(boundary.text or '')
        for boundary in root.findall('.//BOUNDARYREGIONS/B')
    }
    expected_boundaries = {0: {1}, 1: {2}, 2: {103}, 3: {104}}
    if boundary_regions != expected_boundaries:
        raise SystemExit(
            'PRECHECK FAIL: wrong boundary composite mapping: '
            f'expected {expected_boundaries}, got {boundary_regions}'
        )
    return [f'session XML: PASS ({path})', 'implicit 3-D CFS configuration: PASS']


def main() -> None:
    a = parse_args()
    messages = []
    if a.check_programs:
        messages.extend(program_checks())
    if a.geo:
        messages.extend(geo_checks(Path(a.geo)))
    if a.align_periodic:
        messages.extend(align_periodic_faces(
            Path(a.align_periodic), a.periodic_surf1, a.periodic_surf2,
            a.periodic_dir, a.periodic_tolerance,
        ))
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
