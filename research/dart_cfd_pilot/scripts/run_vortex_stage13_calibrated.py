#!/usr/bin/env python3
"""Calibrated Multi-Criterion Core Detector (CMCD) with temporal holdout."""
from __future__ import annotations

import argparse
import csv
import importlib.util
import itertools
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT=Path(__file__).resolve().parents[1]


def load_sibling(name, filename):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).resolve().parent/filename)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module


stage8=load_sibling("stage8","run_dart_stage8_physics_catalogue.py")
base=load_sibling("stage10c","run_vortex_stage10c_absolute_scale.py")


def write_csv(path,rows,fields):
    with path.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


def match_reference(detections,reference,radius):
    remaining=set(range(len(reference)));matched=[]
    for i,d in sorted(enumerate(detections),key=lambda z:-float(z[1]["score"])):
        candidates=[j for j in remaining if int(reference[j]["rotation_sign"])==int(d["sign"])]
        if not candidates:continue
        j=min(candidates,key=lambda k:math.hypot(d["x"]-float(reference[k]["x_physical"]),d["y"]-float(reference[k]["y_physical"])))
        distance=math.hypot(d["x"]-float(reference[j]["x_physical"]),d["y"]-float(reference[j]["y_physical"]))
        if distance<=radius:matched.append((i,j,distance));remaining.remove(j)
    return matched


def close_reference_members(reference,maximum_separation):
    members=set()
    for i,a in enumerate(reference):
        for j in range(i+1,len(reference)):
            b=reference[j]
            if int(a["rotation_sign"])!=int(b["rotation_sign"]):continue
            if math.hypot(float(a["x_physical"])-float(b["x_physical"]),float(a["y_physical"])-float(b["y_physical"]))<=maximum_separation:
                members.update((i,j))
    return members


def detector_cfg(template,values):
    return {"scales":[1.,2.,4.,8.,12.],"minimum_lambda_ci":1.,
            "minimum_absolute_gamma2":values["minimum_absolute_gamma2"],
            "minimum_omega_ratio":values["minimum_omega_ratio"],"minimum_lci_snr":6.,
            "rotation_floor_factor":2.5,"minimum_sign_coherence":.65,"gamma_window_factor":2.,
            "nms_radius_factor":values["nms_radius_factor"],
            "minimum_nms_radius":values["minimum_nms_radius"],
            "analysis_boundary_margin":.1,"maximum_detections":values["maximum_detections"]}


def detect_snapshot(snapshot,cfg):
    detected=base.detect(snapshot["x"],snapshot["y"],snapshot["u"],snapshot["v"],cfg)
    x,y,fluid=snapshot["x"],snapshot["y"],snapshot["fluid"]
    return [d for d in detected if fluid[np.argmin(abs(x-d["x"])),np.argmin(abs(y-d["y"]))]]


def evaluate(snapshots,frame_ids,cfg,match_radius,close_radius,keep_rows=False):
    totals={"detections":0,"reference":0,"matches":0,"close_reference_members":0,"close_matches":0}
    rows=[];per_frame=[]
    for fi in frame_ids:
        s=snapshots[fi];detected=detect_snapshot(s,cfg);reference=s["reference"]
        matches=match_reference(detected,reference,match_radius);matched_det={i:(j,d) for i,j,d in matches}
        close=close_reference_members(reference,close_radius);matched_ref={j for _,j,_ in matches}
        totals["detections"]+=len(detected);totals["reference"]+=len(reference);totals["matches"]+=len(matches)
        totals["close_reference_members"]+=len(close);totals["close_matches"]+=len(close&matched_ref)
        per_frame.append({"frame_index":fi,"source_step":s["step"],"detections":len(detected),"reference":len(reference),
                          "matches":len(matches),"close_reference_members":len(close),"close_matches":len(close&matched_ref)})
        if keep_rows:
            for i,d in enumerate(detected):
                jdist=matched_det.get(i)
                rows.append({"frame_index":fi,"source_step":s["step"],"time":fi*s["snapshot_dt"],
                             "detection_id":f"F{fi:03d}D{i:03d}","x_physical":d["x"],"y_physical":d["y"],
                             "rotation_sign":d["sign"],"radius":d["radius"],"score":d["score"],
                             "lambda_ci":d["lambda_ci"],"gamma2":d["gamma2"],"omega_ratio":d["omega_ratio"],
                             "matched_stage8":jdist is not None,
                             "stage8_reference_id":reference[jdist[0]]["reference_id"] if jdist else "",
                             "reference_distance":jdist[1] if jdist else ""})
    metrics={**totals,"coverage":totals["matches"]/max(totals["reference"],1),
             "detection_to_reference_ratio":totals["detections"]/max(totals["reference"],1),
             "close_member_coverage":totals["close_matches"]/max(totals["close_reference_members"],1)}
    return metrics,rows,per_frame


def draw_overlay(path,snapshot,detections,title,close_radius):
    import matplotlib.pyplot as plt
    x,y,omega,fluid=snapshot["x"],snapshot["y"],snapshot["omega"],snapshot["fluid"];reference=snapshot["reference"]
    masked=np.where(fluid,omega,np.nan);lim=max(float(np.nanpercentile(np.abs(masked),99.5)),1e-8)
    close=close_reference_members(reference,close_radius)
    fig,ax=plt.subplots(figsize=(9.2,7.3),constrained_layout=True)
    cf=ax.contourf(x,y,masked.T,levels=np.linspace(-lim,lim,101),cmap="RdBu_r",extend="both")
    if detections:
        ax.scatter([d["x"] for d in detections],[d["y"] for d in detections],s=64,facecolors="none",
                   edgecolors=["#ffbf00" if d["sign"]>0 else "#39e600" for d in detections],linewidths=1.7,label="CMCD centers")
    if reference:
        ax.scatter([float(r["x_physical"]) for r in reference],[float(r["y_physical"]) for r in reference],
                   marker="+",s=48,c="black",linewidths=1.3,label="criteria-derived reference catalogue")
    if close:
        ax.scatter([float(reference[i]["x_physical"]) for i in sorted(close)],[float(reference[i]["y_physical"]) for i in sorted(close)],
                   marker="s",s=100,facecolors="none",edgecolors="#b400ff",linewidths=1.5,label="close-pair reference members")
    ax.set(xlabel="x",ylabel="y",title=title);ax.set_aspect("equal");ax.legend(loc="upper left",framealpha=.88)
    c=fig.colorbar(cf,ax=ax);c.set_label(r"Spanwise vorticity $\omega_z$")
    fig.savefig(path,dpi=240,bbox_inches="tight");plt.close(fig)


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--case-dir",type=Path,required=True);ap.add_argument("--mfc-root",type=Path,required=True)
    ap.add_argument("--stage8-catalogue",type=Path,required=True);ap.add_argument("--config",type=Path)
    ap.add_argument("--output-dir",type=Path,required=True);args=ap.parse_args()
    cfg=json.loads((args.config or ROOT/"dart_stage13.json").read_text());out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(args.mfc_root.resolve()/"toolchain"));from mfc.viz.reader import assemble,discover_timesteps
    required=list(range(cfg["step_start"],cfg["step_stop"]+1,cfg["step_stride"]));available=discover_timesteps(str(args.case_dir.resolve()),"binary")
    missing=sorted(set(required)-set(available))
    if missing:ap.error(f"raw MFC sequence incomplete: missing {len(missing)}; first={missing[0]}")
    references={}
    with args.stage8_catalogue.open(newline="") as f:
        for row in csv.DictReader(f):references.setdefault(int(row["source_step"]),[]).append(row)
    snapshots={}
    for fi,step in enumerate(required):
        a=assemble(str(args.case_dir.resolve()),step,fmt="binary");absent=sorted({"vel1","vel2","omega3"}-set(a.variables))
        if absent:raise RuntimeError(f"step {step} lacks {absent}")
        xi=np.flatnonzero((a.x_cc>=cfg["analysis_xlim"][0])&(a.x_cc<=cfg["analysis_xlim"][1]));yi=np.flatnonzero((a.y_cc>=cfg["analysis_ylim"][0])&(a.y_cc<=cfg["analysis_ylim"][1]))
        xi=np.arange(max(0,xi[0]-3),min(a.x_cc.size,xi[-1]+4));yi=np.arange(max(0,yi[0]-3),min(a.y_cc.size,yi[-1]+4))
        x,y=a.x_cc[xi].copy(),a.y_cc[yi].copy();u=a.variables["vel1"][np.ix_(xi,yi)].copy();v=a.variables["vel2"][np.ix_(xi,yi)].copy();omega=a.variables["omega3"][np.ix_(xi,yi)].copy()
        fluid=stage8.geometry_fluid_mask(x,y)&(x[:,None]>=cfg["analysis_xlim"][0])&(x[:,None]<=cfg["analysis_xlim"][1])&(y[None,:]>=cfg["analysis_ylim"][0])&(y[None,:]<=cfg["analysis_ylim"][1])
        snapshots[fi]={"x":x,"y":y,"u":u,"v":v,"omega":omega,"fluid":fluid,"reference":references.get(step,[]),"step":step,"snapshot_dt":cfg["snapshot_dt"]}
        del a
    calibration=list(range(cfg["calibration_frame_start"],cfg["calibration_frame_stop"]+1))
    holdout=list(range(cfg["holdout_frame_start"],cfg["holdout_frame_stop"]+1))
    keys=["nms_radius_factor","minimum_nms_radius","minimum_absolute_gamma2","minimum_omega_ratio","maximum_detections"]
    sweep=[]
    for index,values in enumerate(itertools.product(*(cfg["search_grid"][k] for k in keys))):
        choice=dict(zip(keys,values));dcfg=detector_cfg(cfg,choice)
        metrics,_,_=evaluate(snapshots,calibration,dcfg,cfg["reference_match_radius"],cfg["close_pair_maximum_separation"])
        objective=metrics["coverage"]+cfg["close_pair_objective_weight"]*metrics["close_member_coverage"]-cfg["candidate_penalty"]*max(metrics["detection_to_reference_ratio"]-cfg["target_maximum_detection_to_reference_ratio"],0)
        sweep.append({"configuration_id":index,**choice,**metrics,"objective":objective,
                      "ratio_feasible":metrics["detection_to_reference_ratio"]<=cfg["target_maximum_detection_to_reference_ratio"]})
    feasible=[r for r in sweep if r["ratio_feasible"]]
    pool=feasible or sweep;selected=max(pool,key=lambda r:(r["objective"],r["coverage"],r["close_member_coverage"],-r["detection_to_reference_ratio"]))
    selected_values={k:selected[k] for k in keys};selected_cfg=detector_cfg(cfg,selected_values)
    calibration_metrics,_,_=evaluate(snapshots,calibration,selected_cfg,cfg["reference_match_radius"],cfg["close_pair_maximum_separation"])
    holdout_metrics,_,_=evaluate(snapshots,holdout,selected_cfg,cfg["reference_match_radius"],cfg["close_pair_maximum_separation"])
    full_metrics,rows,per_frame=evaluate(snapshots,list(range(len(required))),selected_cfg,cfg["reference_match_radius"],cfg["close_pair_maximum_separation"],True)
    selected_frames=[cfg["calibration_frame_stop"],(cfg["holdout_frame_start"]+cfg["holdout_frame_stop"])//2,cfg["holdout_frame_stop"]]
    by_frame={fi:detect_snapshot(snapshots[fi],selected_cfg) for fi in selected_frames}
    for fi in selected_frames:
        draw_overlay(out/f"stage13_frame_{fi:04d}.png",snapshots[fi],by_frame[fi],
                     f"MFC/ILES CMCD: frame {fi}, step {snapshots[fi]['step']}",cfg["close_pair_maximum_separation"])
    write_csv(out/"stage13_sweep.csv",sweep,list(sweep[0]))
    fields=["frame_index","source_step","time","detection_id","x_physical","y_physical","rotation_sign","radius","score","lambda_ci","gamma2","omega_ratio","matched_stage8","stage8_reference_id","reference_distance"]
    write_csv(out/"stage13_detections.csv",rows,fields);write_csv(out/"stage13_per_frame.csv",per_frame,list(per_frame[0]))
    gates={"temporal_holdout":"pass","holdout_stage8_coverage":"pass" if holdout_metrics["coverage"]>=cfg["minimum_holdout_stage8_coverage"] else "fail",
           "holdout_candidate_growth":"pass" if holdout_metrics["detection_to_reference_ratio"]<=cfg["maximum_holdout_detection_to_reference_ratio"] else "fail",
           "holdout_close_member_coverage":"pass" if holdout_metrics["close_member_coverage"]>=cfg["minimum_holdout_close_member_coverage"] else "fail"}
    report={"schema_version":1,"method_name":"Calibrated Multi-Criterion Core Detector (CMCD)","status":"completed","created_at_utc":datetime.now(timezone.utc).isoformat(),
            "calibration_frames":calibration,"holdout_frames":holdout,"grid_configurations":len(sweep),
            "selected_configuration":selected_cfg,"calibration_metrics":calibration_metrics,
            "holdout_metrics":holdout_metrics,"full_sequence_metrics":full_metrics,"gates":gates,
            "claim_gate":"holdout_close_core_detector_requires_independent_manual_labels",
            "limitations":["Stage 8 is a physics baseline and not exhaustive ground truth.","Configuration selection uses only frames 1-30; reported acceptance gates use frames 31-60.",
                           "Close-pair recovery counts reference members in same-sign pairs within the predeclared physical separation.",
                           "Independent expert labels are still required before a publication-level precision claim."]}
    (out/"stage13_report.json").write_text(json.dumps(report,indent=2)+"\n")
    print("STAGE13_STATUS=completed");print(f"STAGE13_GRID={len(sweep)}");print(f"STAGE13_SELECTED={json.dumps(selected_values,sort_keys=True)}")
    print(f"STAGE13_HOLDOUT_COVERAGE={holdout_metrics['coverage']:.6f}");print(f"STAGE13_HOLDOUT_RATIO={holdout_metrics['detection_to_reference_ratio']:.6f}")
    print(f"STAGE13_HOLDOUT_CLOSE={holdout_metrics['close_member_coverage']:.6f}");print(f"STAGE13_CLAIM_GATE={report['claim_gate']}")
    print(f"STAGE13_REPORT={out/'stage13_report.json'}");return 0


if __name__=="__main__":raise SystemExit(main())
