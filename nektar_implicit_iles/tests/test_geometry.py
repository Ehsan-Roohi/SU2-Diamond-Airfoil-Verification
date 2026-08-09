#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('generate_geo', ROOT / 'geometry/generate_geo.py')
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

fillets = module.rounded_vertices(0.001)
assert len(fillets) == 4
for incoming, outgoing, center in fillets:
    assert math.isclose(math.dist(incoming, center), 0.001, rel_tol=0, abs_tol=1e-13)
    assert math.isclose(math.dist(outgoing, center), 0.001, rel_tol=0, abs_tol=1e-13)

print('geometry fillet test: PASS')

