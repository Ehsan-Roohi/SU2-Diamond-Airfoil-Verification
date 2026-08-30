import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy", reason="Stage 10E numerical tests require SciPy")

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("stage10e",ROOT/"scripts/run_vortex_stage10e_deblend.py")
e=importlib.util.module_from_spec(SPEC);sys.modules["stage10e"]=e;SPEC.loader.exec_module(e)
CFG={"roi_radius":.42,"minimum_bic_gain":10000.,"minimum_improvement":.95,"minimum_amplitude_ratio":.18,
     "minimum_separation":.14,"minimum_normalized_separation":1.1}


def test_close_merger_is_deblended_without_false_core():
    x=np.linspace(-1,1,161);y=np.linspace(-.8,.8,129);xx,yy=np.meshgrid(x,y,indexing="ij")
    truth=[e.base.Vortex(-.11,0.,1.,.12),e.base.Vortex(.11,0.,.85,.11)]
    u,v=e.base.add_lamb_oseen(xx,yy,truth)
    d=e.detect_deblended(x,y,u,v,CFG)
    tp,fp,fn,_=e.base.match(d,truth)
    assert (tp,fp,fn)==(2,0,0)


def test_single_core_is_not_split():
    x=np.linspace(-1,1,161);y=np.linspace(-.8,.8,129);xx,yy=np.meshgrid(x,y,indexing="ij")
    truth=[e.base.Vortex(.05,-.08,-1.,.13)]
    u,v=e.base.add_lamb_oseen(xx,yy,truth)
    d=e.detect_deblended(x,y,u,v,CFG)
    tp,fp,fn,_=e.base.match(d,truth)
    assert (tp,fp,fn)==(1,0,0)
