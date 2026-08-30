#!/usr/bin/env python3
"""Apply frozen Stage 10E close-core detection to the 61 raw MFC/ILES snapshots."""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def load_sibling(name, filename):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).resolve().parent/filename)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module


stage8=load_sibling("stage8", "run_dart_stage8_physics_catalogue.py")
stage10e=load_sibling("stage10e", "run_vortex_stage10e_deblend.py")

DEBLEND_CFG={"roi_radius":.42,"minimum_bic_gain":10000.,"minimum_improvement":.95,
             "minimum_amplitude_ratio":.18,"minimum_separation":.14,
             "minimum_normalized_separation":1.1}


def write_csv(path, rows, fields):
    with path.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


def match_reference(detections, reference, radius):
    remaining=set(range(len(reference)));matched=[]
    for i,d in sorted(enumerate(detections),key=lambda z:-z[1].get("score",0)):
        candidates=[j for j in remaining if int(reference[j]["rotation_sign"])==int(d["sign"])]
        if not candidates:continue
        j=min(candidates,key=lambda k:math.hypot(d["x"]-float(reference[k]["x_physical"]),d["y"]-float(reference[k]["y_physical"])))
        distance=math.hypot(d["x"]-float(reference[j]["x_physical"]),d["y"]-float(reference[j]["y_physical"]))
        if distance<=radius:matched.append((i,j,distance));remaining.remove(j)
    return matched


def draw_overlay(path,x,y,omega,fluid,detections,reference,title):
    import matplotlib.pyplot as plt
    masked=np.where(fluid,omega,np.nan);lim=float(np.nanpercentile(np.abs(masked),99.5));lim=max(lim,1e-8)
    fig,ax=plt.subplots(figsize=(9.2,7.3),constrained_layout=True)
    cf=ax.contourf(x,y,masked.T,levels=np.linspace(-lim,lim,101),cmap="RdBu_r",extend="both")
    for k,d in enumerate(detections):
        color="#00ef72" if not d.get("deblended") else "#ffd400"
        ax.plot(d["x"],d["y"],"o",ms=8,mfc="none",mec=color,mew=1.7,label=("Stage 10E center" if k==0 else None))
    if reference:
        ax.scatter([float(r["x_physical"]) for r in reference],[float(r["y_physical"]) for r in reference],
                   marker="+",s=45,c="black",linewidths=1.3,label="Stage 8 physics catalogue")
    ax.set(xlabel="x",ylabel="y",title=title);ax.set_aspect("equal");ax.legend(loc="upper left",framealpha=.88)
    c=fig.colorbar(cf,ax=ax);c.set_label(r"Spanwise vorticity $\omega_z$")
    fig.savefig(path,dpi=240,bbox_inches="tight");plt.close(fig)


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--case-dir",type=Path,required=True);ap.add_argument("--mfc-root",type=Path,required=True)
    ap.add_argument("--stage8-catalogue",type=Path);ap.add_argument("--config",type=Path);ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--max-frames",type=int,default=0);args=ap.parse_args()
    cfg=json.loads((args.config or ROOT/"dart_stage11.json").read_text());out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(args.mfc_root.resolve()/"toolchain"));from mfc.viz.reader import assemble,discover_timesteps
    required=list(range(cfg["step_start"],cfg["step_stop"]+1,cfg["step_stride"]));available=discover_timesteps(str(args.case_dir.resolve()),"binary")
    missing=sorted(set(required)-set(available));
    if missing:ap.error(f"raw MFC sequence incomplete: missing {len(missing)}; first={missing[0]}")
    steps=required[:args.max_frames or None];references={}
    if args.stage8_catalogue and args.stage8_catalogue.is_file():
        with args.stage8_catalogue.open(newline="") as f:
            for row in csv.DictReader(f):references.setdefault(int(row["source_step"]),[]).append(row)
    rows=[];per_frame=[];selected={0,len(steps)//2,len(steps)-1}
    for fi,step in enumerate(steps):
        a=assemble(str(args.case_dir.resolve()),step,fmt="binary");absent=sorted({"vel1","vel2","omega3"}-set(a.variables))
        if absent:raise RuntimeError(f"step {step} lacks {absent}")
        xi=np.flatnonzero((a.x_cc>=cfg["analysis_xlim"][0])&(a.x_cc<=cfg["analysis_xlim"][1]));yi=np.flatnonzero((a.y_cc>=cfg["analysis_ylim"][0])&(a.y_cc<=cfg["analysis_ylim"][1]))
        xi=np.arange(max(0,xi[0]-3),min(a.x_cc.size,xi[-1]+4));yi=np.arange(max(0,yi[0]-3),min(a.y_cc.size,yi[-1]+4))
        x,y=a.x_cc[xi],a.y_cc[yi];u=a.variables["vel1"][np.ix_(xi,yi)];v=a.variables["vel2"][np.ix_(xi,yi)];omega=a.variables["omega3"][np.ix_(xi,yi)]
        fluid=stage8.geometry_fluid_mask(x,y)&(x[:,None]>=cfg["analysis_xlim"][0])&(x[:,None]<=cfg["analysis_xlim"][1])&(y[None,:]>=cfg["analysis_ylim"][0])&(y[None,:]<=cfg["analysis_ylim"][1])
        detections=[d for d in stage10e.detect_deblended(x,y,u,v,DEBLEND_CFG) if fluid[np.argmin(abs(x-d["x"])),np.argmin(abs(y-d["y"]))]]
        ref=references.get(step,[]);matches=match_reference(detections,ref,cfg["reference_match_radius"])
        matched_det={i:(j,dist) for i,j,dist in matches}
        for i,d in enumerate(detections):
            jdist=matched_det.get(i);rows.append({"frame_index":fi,"source_step":step,"time":fi*cfg["snapshot_dt"],"detection_id":f"F{fi:03d}D{i:03d}",
                "x_physical":d["x"],"y_physical":d["y"],"rotation_sign":d["sign"],"radius":d["radius"],"score":d["score"],"lambda_ci":d["lambda_ci"],
                "gamma2":d["gamma2"],"omega_ratio":d["omega_ratio"],"deblended":bool(d.get("deblended",False)),
                "matched_stage8":jdist is not None,"stage8_reference_id":ref[jdist[0]]["reference_id"] if jdist else "","reference_distance":jdist[1] if jdist else ""})
        per_frame.append({"frame_index":fi,"source_step":step,"detections":len(detections),"deblended":sum(bool(d.get("deblended",False)) for d in detections),
                          "stage8_reference":len(ref),"matched_stage8":len(matches),"additional_candidates":len(detections)-len(matches)})
        if fi in selected:draw_overlay(out/f"stage11_frame_{fi:04d}.png",x,y,omega,fluid,detections,ref,f"MFC/ILES Stage 11: frame {fi}, step {step}")
    fields=["frame_index","source_step","time","detection_id","x_physical","y_physical","rotation_sign","radius","score","lambda_ci","gamma2","omega_ratio","deblended","matched_stage8","stage8_reference_id","reference_distance"]
    write_csv(out/"stage11_detections.csv",rows,fields);write_csv(out/"stage11_per_frame.csv",per_frame,list(per_frame[0]))
    total_ref=sum(r["stage8_reference"] for r in per_frame);total_match=sum(r["matched_stage8"] for r in per_frame);total_det=len(rows)
    report={"schema_version":1,"status":"completed","created_at_utc":datetime.now(timezone.utc).isoformat(),"frames":len(steps),"detections":total_det,
            "deblended_detections":sum(bool(r["deblended"]) for r in rows),"stage8_reference_rows":total_ref,"matched_stage8_rows":total_match,
            "stage8_coverage":total_match/max(total_ref,1),"additional_candidates":total_det-total_match,
            "gates":{"raw_sequence_complete":"pass","finite_output":"pass" if all(np.isfinite(float(r["x_physical"])) for r in rows) else "fail",
                     "stage8_coverage":"pass" if total_match/max(total_ref,1)>=cfg["minimum_stage8_coverage"] else "fail",
                     "candidate_growth":"pass" if total_det/max(total_ref,1)<=cfg["maximum_detection_to_reference_ratio"] else "fail"},
            "claim_gate":"real_field_candidate_catalogue_requires_visual_audit","limitations":["Stage 8 is a physics baseline, not exhaustive ground truth.","Additional candidates require manual audit before being counted as recovered vortices.","This is two-dimensional core detection, not 3-D vortex-tube segmentation."]}
    (out/"stage11_report.json").write_text(json.dumps(report,indent=2)+"\n")
    print("STAGE11_STATUS=completed");print(f"STAGE11_DETECTIONS={total_det}");print(f"STAGE11_DEBLENDED={report['deblended_detections']}");print(f"STAGE11_STAGE8_COVERAGE={report['stage8_coverage']:.6f}");print(f"STAGE11_ADDITIONAL={report['additional_candidates']}");print(f"STAGE11_REPORT={out/'stage11_report.json'}")
    return 0


if __name__=="__main__":raise SystemExit(main())
