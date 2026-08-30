#!/usr/bin/env python3
"""Physics-based two-core deblending layered on the frozen Stage 10C detector."""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.optimize import least_squares


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("stage10c", HERE / "run_vortex_stage10c_absolute_scale.py")
base = importlib.util.module_from_spec(SPEC)
sys.modules["stage10c"] = base
SPEC.loader.exec_module(base)

BASE_CFG = {
    "scales": [1., 2., 4., 8., 12.], "minimum_lambda_ci": 1.,
    "minimum_absolute_gamma2": .8, "minimum_omega_ratio": .7,
    "minimum_lci_snr": 6., "rotation_floor_factor": 2.5,
    "minimum_sign_coherence": .65, "gamma_window_factor": 2.,
    "nms_radius_factor": 2., "minimum_nms_radius": .08,
    "analysis_boundary_margin": .1,
}


def gaussian1(p, xx, yy):
    amp, x0, y0, rad, offset = p
    return offset + amp * np.exp(-((xx-x0)**2 + (yy-y0)**2) / max(rad*rad, 1e-10))


def gaussian2(p, xx, yy):
    a1,x1,y1,r1,a2,x2,y2,r2,offset = p
    return offset + a1*np.exp(-((xx-x1)**2+(yy-y1)**2)/max(r1*r1,1e-10)) + a2*np.exp(-((xx-x2)**2+(yy-y2)**2)/max(r2*r2,1e-10))


def fit_models(x, y, signed_omega, q, roi_radius=.42):
    maskx=np.abs(x-q["x"])<=roi_radius; masky=np.abs(y-q["y"])<=roi_radius
    xp=x[maskx]; yp=y[masky]; z=np.maximum(signed_omega[np.ix_(maskx,masky)],0)
    if xp.size<9 or yp.size<9 or float(z.max())<=0:return None
    # Downsample only for nonlinear fitting; all coordinates remain physical.
    sx=max(1,xp.size//35); sy=max(1,yp.size//35); xp=xp[::sx]; yp=yp[::sy]; z=z[::sx,::sy]
    xx,yy=np.meshgrid(xp,yp,indexing="ij"); peak=float(z.max()); floor=float(np.percentile(z,10))
    w=np.maximum(z-floor,0); sw=float(w.sum());
    if sw<=0:return None
    mx=float((w*xx).sum()/sw); my=float((w*yy).sum()/sw)
    dx=xx-mx; dy=yy-my
    cov=np.array([[(w*dx*dx).sum()/sw,(w*dx*dy).sum()/sw],[(w*dx*dy).sum()/sw,(w*dy*dy).sum()/sw]])
    vals,vecs=np.linalg.eigh(cov); direction=vecs[:,int(np.argmax(vals))]; spread=max(math.sqrt(max(float(vals.max()),1e-6)),.05)
    lo1=[0,xp.min(),yp.min(),.025,0]; hi1=[2.5*peak,xp.max(),yp.max(),.38,.35*peak]
    p1=[peak,mx,my,min(max(spread,.06),.25),floor]
    f1=least_squares(lambda p:(gaussian1(p,xx,yy)-z).ravel(),p1,bounds=(lo1,hi1),max_nfev=180)
    delta=min(max(.55*spread,.035),.16); c1=np.array([mx,my])-delta*direction; c2=np.array([mx,my])+delta*direction
    p2=[.55*peak,c1[0],c1[1],max(.7*spread,.045),.55*peak,c2[0],c2[1],max(.7*spread,.045),floor]
    lo2=[0,xp.min(),yp.min(),.025,0,xp.min(),yp.min(),.025,0]
    hi2=[2.5*peak,xp.max(),yp.max(),.34,2.5*peak,xp.max(),yp.max(),.34,.35*peak]
    f2=least_squares(lambda p:(gaussian2(p,xx,yy)-z).ravel(),p2,bounds=(lo2,hi2),max_nfev=260)
    rss1=float(np.sum(f1.fun*f1.fun)); rss2=float(np.sum(f2.fun*f2.fun)); n=z.size
    bic1=n*math.log(max(rss1/n,1e-20))+5*math.log(n); bic2=n*math.log(max(rss2/n,1e-20))+9*math.log(n)
    a1,x1,y1,r1,a2,x2,y2,r2,_=f2.x
    sep=math.hypot(x1-x2,y1-y2); improvement=1-rss2/max(rss1,1e-20)
    return {"bic_gain":bic1-bic2,"improvement":improvement,"amplitude_ratio":min(a1,a2)/max(a1,a2,1e-20),
            "separation":sep,"normalized_separation":sep/max(.5*(r1+r2),1e-20),
            "cores":[{"x":x1,"y":y1,"radius":r1,"amplitude":a1},{"x":x2,"y":y2,"radius":r2,"amplitude":a2}]}


def detect_deblended(x, y, u, v, cfg):
    detections=base.detect(x,y,u,v,BASE_CFG)
    omega=np.gradient(v,x,axis=0,edge_order=2)-np.gradient(u,y,axis=1,edge_order=2)
    out=[]
    for q in detections:
        fit=fit_models(x,y,q["sign"]*omega,q,cfg["roi_radius"])
        split=(fit is not None and fit["bic_gain"]>=cfg["minimum_bic_gain"] and
               fit["improvement"]>=cfg["minimum_improvement"] and
               fit["amplitude_ratio"]>=cfg["minimum_amplitude_ratio"] and
               fit["separation"]>=cfg["minimum_separation"] and
               fit["normalized_separation"]>=cfg["minimum_normalized_separation"])
        if not split:
            out.append(q); continue
        for core in fit["cores"]:
            z=dict(q); z.update(x=float(core["x"]),y=float(core["y"]),radius=float(core["radius"]),
                              deblended=True,bic_gain=float(fit["bic_gain"]),fit_improvement=float(fit["improvement"]))
            out.append(z)
    # Final sign-aware suppression; fitted siblings are intentionally retained.
    accepted=[]
    for q in sorted(out,key=lambda z:-z.get("score",0)):
        if any(not(q.get("deblended") and a.get("deblended")) and q["sign"]==a["sign"] and
               math.hypot(q["x"]-a["x"],q["y"]-a["y"])<.045 for a in accepted):continue
        accepted.append(q)
    return accepted


def evaluate(data, x, y, cfg):
    rows=[]
    for i,(family,u,v,truth) in enumerate(data):
        d=detect_deblended(x,y,u,v,cfg); tp,fp,fn,dist=base.match(d,truth)
        rows.append({"case":i,"family":family,"truth":len(truth),"detections":len(d),"tp":tp,"fp":fp,"fn":fn,
                     "matched":len(dist),"normalized_squared_error_sum":sum(z*z for z in dist)})
    return base.aggregate(rows),rows


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--output-dir",type=Path,required=True); a=ap.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True)
    x=np.linspace(-1,1,161); y=np.linspace(-.8,.8,129)
    train=base.specs(80,20260905,x,y); test=base.specs(240,20260906,x,y)
    # Predeclared physical split gate. It is deliberately frozen before the test seed.
    cfg={"roi_radius":.42,"minimum_bic_gain":10000.,"minimum_improvement":.95,"minimum_amplitude_ratio":.18,
         "minimum_separation":.14,"minimum_normalized_separation":1.1}
    train_metrics,train_rows=evaluate(train,x,y,cfg)
    train_merger=base.aggregate([r for r in train_rows if r["family"]=="merger"])
    train_resolved=base.aggregate([r for r in train_rows if r["family"]!="merger"])
    test_metrics,rows=evaluate(test,x,y,cfg); fam={k:base.aggregate([r for r in rows if r["family"]==k]) for k in sorted(set(r["family"] for r in rows))}
    report={"status":"completed","selected":cfg,"train_metrics":train_metrics,"train_merger":train_merger,
            "test_metrics":test_metrics,"family_metrics":fam,
            "gates":{"merger_recall":fam["merger"]["recall"]>=.70,"merger_f1":fam["merger"]["f1"]>=.75,
                     "overall_precision":test_metrics["precision"]>=.90},
            "claim_gate":"pass" if fam["merger"]["recall"]>=.70 and fam["merger"]["f1"]>=.75 and test_metrics["precision"]>=.90 else "fail"}
    (a.output_dir/"stage10e_report.json").write_text(json.dumps(report,indent=2)+"\n")
    with (a.output_dir/"stage10e_cases.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    print(json.dumps(report,indent=2)); return 0


if __name__=="__main__": raise SystemExit(main())
