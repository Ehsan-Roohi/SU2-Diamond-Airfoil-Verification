#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preflight', ROOT / 'scripts/preflight.py')
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

assert module.composite_refs('C[1,3-5] C[100-101]') == {1, 3, 4, 5, 100, 101}

mesh_xml = '''<?xml version="1.0"?>
<NEKTAR>
  <GEOMETRY DIM="3" SPACE="3">
    <VERTEX>
      <V ID="0">0 0 0</V><V ID="1">1 0 0</V><V ID="2">2 0 0</V>
      <V ID="3">0 1 0</V><V ID="4">1 1 0</V><V ID="5">2 1 0</V>
      <V ID="6">0 0 0.1</V><V ID="7">1 0 0.1</V><V ID="8">2 0 0.1</V>
      <V ID="9">0 1 0.1</V><V ID="10">1 1 0.1</V><V ID="11">2 1 0.1</V>
    </VERTEX>
    <EDGE>
      <E ID="0">0 1</E><E ID="1">1 4</E><E ID="2">4 3</E><E ID="3">3 0</E>
      <E ID="4">1 2</E><E ID="5">2 4</E>
      <E ID="10">6 7</E><E ID="11">7 10</E><E ID="12">10 9</E><E ID="13">9 6</E>
      <E ID="14">7 8</E><E ID="15">8 10</E>
    </EDGE>
    <FACE>
      <Q ID="10">0 1 2 3</Q><T ID="11">4 5 1</T>
      <Q ID="20">10 11 12 13</Q><T ID="21">14 15 11</T>
    </FACE>
    <COMPOSITE>
      <C ID="1"> F[10] </C><C ID="2"> F[11] </C>
      <C ID="100"> P[0] </C><C ID="101"> H[0] </C>
      <C ID="103"> F[10-11] </C><C ID="104"> F[21,20] </C>
    </COMPOSITE>
    <DOMAIN><D ID="0"> C[100-101] </D></DOMAIN>
  </GEOMETRY>
</NEKTAR>
'''

with tempfile.TemporaryDirectory() as tmp:
    mesh_path = Path(tmp) / 'mesh3d.xml'
    mesh_path.write_text(mesh_xml, encoding='utf-8')
    alignment = module.align_periodic_faces(mesh_path)
    assert '2 pairs' in alignment[0]
    assert 'reordered=2' in alignment[0]
    aligned_root = module.ET.parse(mesh_path).getroot()
    aligned_surface = next(
        comp for comp in aligned_root.findall('.//COMPOSITE/C') if comp.attrib['ID'] == '104'
    )
    assert module.ordered_entity_refs(aligned_surface.text or '', 'F') == [20, 21]
    assert 'reordered=0' in module.align_periodic_faces(mesh_path)[0]
    messages = module.mesh_checks(mesh_path)
    assert messages[0].startswith('mesh XML: PASS')

session_messages = module.session_checks(ROOT / 'validation/stage_start_a4.xml')
assert session_messages[0].startswith('session XML: PASS')

print('preflight composite test: PASS')
