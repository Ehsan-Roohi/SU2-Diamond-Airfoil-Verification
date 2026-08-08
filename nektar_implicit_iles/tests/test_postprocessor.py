#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
alpha = math.radians(4.0)
span = 0.1
qarea = 0.5 * span
cd, cl = 0.03, 0.08

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    fce = tmp_path / 'forces.fce'
    rows = ['# Time F1-press F1-visc F1-total F2-press F2-visc F2-total F3-press F3-visc F3-total']
    for i in range(201):
        t = i * 0.01
        cd_i = cd + 0.0002 * math.sin(2 * math.pi * t)
        cl_i = cl + 0.0003 * math.cos(2 * math.pi * t)
        drag, lift = qarea * cd_i, qarea * cl_i
        fx = drag * math.cos(alpha) - lift * math.sin(alpha)
        fy = drag * math.sin(alpha) + lift * math.cos(alpha)
        values = [t, fx, 0, fx, fy, 0, fy, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        rows.append(' '.join(f'{v:.16g}' for v in values))
    fce.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    subprocess.run(
        [
            'python3', str(ROOT / 'post/analyze_forces.py'), str(fce),
            '--alpha', '4', '--span', str(span), '--window', '1.5',
            '--output-dir', str(tmp_path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    summary = json.loads((tmp_path / 'force_summary.json').read_text(encoding='utf-8'))
    assert summary['status'] == 'PASS'
    assert abs(summary['statistics']['CD']['mean'] - cd) < 5e-4
    assert abs(summary['statistics']['CL']['mean'] - cl) < 5e-4

print('force postprocessor test: PASS')

