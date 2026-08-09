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
    <COMPOSITE>
      <C ID="1"> F[0] </C><C ID="2"> F[1] </C>
      <C ID="100"> P[0] </C><C ID="101"> H[0] </C>
      <C ID="103"> F[2] </C><C ID="104"> F[3] </C>
    </COMPOSITE>
    <DOMAIN><D ID="0"> C[100-101] </D></DOMAIN>
  </GEOMETRY>
</NEKTAR>
'''

with tempfile.TemporaryDirectory() as tmp:
    mesh_path = Path(tmp) / 'mesh3d.xml'
    mesh_path.write_text(mesh_xml, encoding='utf-8')
    messages = module.mesh_checks(mesh_path)
    assert messages[0].startswith('mesh XML: PASS')

session_messages = module.session_checks(ROOT / 'validation/stage_start_a4.xml')
assert session_messages[0].startswith('session XML: PASS')

print('preflight composite test: PASS')
