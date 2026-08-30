#!/usr/bin/env python3
"""Compare Stage 13 with calibrated physical baselines and build a blind audit pack."""
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
from scipy.ndimage import maximum_filter


ROOT=Path(__file__).resolve().parents[1]


def load_sibling(name,filename):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).resolve().parent/filename)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module


stage8=load_sibling("stage8","run_dart_stage8_physics_catalogue.py")


def write_csv(path,rows,fields):
    with path.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


def match_reference(detections,reference,radius):
    remaining=set(range(len(reference)));matched=[]
    for i,d in sorted(enumerate(detections),key=lambda z:-float(z[1].get("score",0))):
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


def derive_fields(x,y,u,v):
    dux,duy=np.gradient(u,x,y,edge_order=2);dvx,dvy=np.gradient(v,x,y,edge_order=2)
    omega=dvx-duy;trace=dux+dvy;det=dux*dvy-duy*dvx;disc=trace*trace-4*det
    lci=.5*np.sqrt(np.maximum(-disc,0))
    sxy=.5*(duy+dvx);strain2=dux*dux+dvy*dvy+2*sxy*sxy
    rotation2=.5*omega*omega;q=.5*(rotation2-strain2)
    return {"q":np.maximum(q,0),"lci":lci,"omega_abs":np.abs(omega),"omega":omega}


def baseline_detect(snapshot,method,parameters):
    score=snapshot[method];fluid=snapshot["fluid"];finite=score[fluid&np.isfinite(score)]
    med=float(np.median(finite));mad=float(np.median(np.abs(finite-med)))
    threshold=med+float(parameters["snr"])*1.4826*max(mad,1e-12)
    peaks=(score==maximum_filter(score,size=3,mode="nearest"))&(score>=threshold)&fluid
    x,y=snapshot["x"],snapshot["y"];candidates=[]
    for i,j in np.argwhere(peaks):
        if i<2 or j<2 or i>=len(x)-2 or j>=len(y)-2:continue
        candidates.append({"x":float(x[i]),"y":float(y[j]),"sign":1 if snapshot["omega"][i,j]>=0 else -1,
                           "score":float(score[i,j]),"method":method})
    accepted=[];radius=float(parameters["nms_radius"])
    for candidate in sorted(candidates,key=lambda d:-d["score"]):
        if any(candidate["sign"]==a["sign"] and math.hypot(candidate["x"]-a["x"],candidate["y"]-a["y"])<radius for a in accepted):continue
        accepted.append(candidate)
        if len(accepted)>=int(parameters["maximum_detections"]):break
    return accepted


def evaluate(detector,snapshots,frame_ids,match_radius,close_radius):
    totals={"detections":0,"reference":0,"matches":0,"close_reference_members":0,"close_matches":0}
    frame_detections={}
    for fi in frame_ids:
        reference=snapshots[fi]["reference"];detections=detector(fi);frame_detections[fi]=detections
        matches=match_reference(detections,reference,match_radius);matched_ref={j for _,j,_ in matches}
        close=close_reference_members(reference,close_radius)
        totals["detections"]+=len(detections);totals["reference"]+=len(reference);totals["matches"]+=len(matches)
        totals["close_reference_members"]+=len(close);totals["close_matches"]+=len(close&matched_ref)
    metrics={**totals,"coverage":totals["matches"]/max(totals["reference"],1),
             "detection_to_reference_ratio":totals["detections"]/max(totals["reference"],1),
             "close_member_coverage":totals["close_matches"]/max(totals["close_reference_members"],1)}
    return metrics,frame_detections


def calibrate_method(method,snapshots,calibration,cfg):
    keys=["snr","nms_radius","maximum_detections"];rows=[]
    for index,values in enumerate(itertools.product(*(cfg["baseline_grid"][k] for k in keys))):
        choice=dict(zip(keys,values))
        metrics,_=evaluate(lambda fi:baseline_detect(snapshots[fi],method,choice),snapshots,calibration,
                           cfg["reference_match_radius"],cfg["close_pair_maximum_separation"])
        objective=metrics["coverage"]+cfg["close_pair_objective_weight"]*metrics["close_member_coverage"]-cfg["candidate_penalty"]*max(metrics["detection_to_reference_ratio"]-cfg["target_maximum_detection_to_reference_ratio"],0)
        rows.append({"method":method,"configuration_id":index,**choice,**metrics,"objective":objective,
                     "ratio_feasible":metrics["detection_to_reference_ratio"]<=cfg["target_maximum_detection_to_reference_ratio"]})
    feasible=[r for r in rows if r["ratio_feasible"]];pool=feasible or rows
    selected=max(pool,key=lambda r:(r["objective"],r["coverage"],r["close_member_coverage"],-r["detection_to_reference_ratio"]))
    parameters={k:selected[k] for k in keys}
    return parameters,rows


def load_stage13(path):
    by_frame={}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            by_frame.setdefault(int(row["frame_index"]),[]).append({"x":float(row["x_physical"]),"y":float(row["y_physical"]),
                "sign":int(row["rotation_sign"]),"score":float(row["score"]),"method":"stage13"})
    return by_frame


def consensus_components(method_detections,radius):
    nodes=[]
    for method,detections in method_detections.items():
        for d in detections:nodes.append({**d,"method":method})
    parent=list(range(len(nodes)))
    def find(a):
        while parent[a]!=a:parent[a]=parent[parent[a]];a=parent[a]
        return a
    def union(a,b):
        a,b=find(a),find(b)
        if a!=b:parent[b]=a
    for i,a in enumerate(nodes):
        for j in range(i+1,len(nodes)):
            b=nodes[j]
            if a["method"]==b["method"] or a["sign"]!=b["sign"]:continue
            if math.hypot(a["x"]-b["x"],a["y"]-b["y"])<=radius:union(i,j)
    groups={}
    for i,node in enumerate(nodes):groups.setdefault(find(i),[]).append(node)
    accepted=[];audit=[]
    for group in groups.values():
        methods=sorted(set(d["method"] for d in group));center={"x":float(np.median([d["x"] for d in group])),
            "y":float(np.median([d["y"] for d in group])),"sign":int(group[0]["sign"]),
            "score":float(len(methods)),"method":"consensus","support_methods":";".join(methods),"support_count":len(methods)}
        audit.append(center)
        if len(methods)>=2:accepted.append(center)
    return accepted,audit


def pairwise_agreement(a,b,radius):
    matches=match_reference(a,[{"rotation_sign":d["sign"],"x_physical":d["x"],"y_physical":d["y"]} for d in b],radius)
    return len(matches),len(a),len(b)


def draw_comparison(path,snapshot,methods,title):
    import matplotlib.pyplot as plt
    masked=np.where(snapshot["fluid"],snapshot["omega"],np.nan);lim=max(float(np.nanpercentile(np.abs(masked),99.5)),1e-8)
    fig,axes=plt.subplots(2,2,figsize=(13,10),sharex=True,sharey=True,constrained_layout=True)
    labels=[("stage13","Stage 13"),("q","Q criterion"),("lci","Swirling strength"),("omega_abs","Vorticity extrema")]
    for ax,(key,label) in zip(axes.flat,labels):
        ax.contourf(snapshot["x"],snapshot["y"],masked.T,levels=np.linspace(-lim,lim,81),cmap="RdBu_r",extend="both")
        d=methods[key]
        if d:ax.scatter([z["x"] for z in d],[z["y"] for z in d],s=45,facecolors="none",edgecolors="#ffe000",linewidths=1.3)
        ax.scatter([float(r["x_physical"]) for r in snapshot["reference"]],[float(r["y_physical"]) for r in snapshot["reference"]],
                   marker="+",s=32,c="black",linewidths=1.1)
        ax.set_title(label);ax.set_aspect("equal");ax.set(xlabel="x",ylabel="y")
    fig.suptitle(title);fig.savefig(path,dpi=220,bbox_inches="tight");plt.close(fig)


def draw_blind_crop(path,snapshot,x0,y0,half_width):
    import matplotlib.pyplot as plt
    x,y=snapshot["x"],snapshot["y"];ix=(x>=x0-half_width)&(x<=x0+half_width);iy=(y>=y0-half_width)&(y<=y0+half_width)
    field=np.where(snapshot["fluid"][np.ix_(ix,iy)],snapshot["omega"][np.ix_(ix,iy)],np.nan)
    lim=max(float(np.nanpercentile(np.abs(field),99)),1e-8)
    fig,ax=plt.subplots(figsize=(4.2,4.2),constrained_layout=True)
    ax.contourf(x[ix]-x0,y[iy]-y0,field.T,levels=np.linspace(-lim,lim,81),cmap="RdBu_r",extend="both")
    ax.plot(0,0,"+",color=".35",ms=9,mew=1);ax.set(xlabel=r"$\Delta x$",ylabel=r"$\Delta y$");ax.set_aspect("equal")
    fig.savefig(path,dpi=220,bbox_inches="tight");plt.close(fig)


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--case-dir",type=Path,required=True);ap.add_argument("--mfc-root",type=Path,required=True)
    ap.add_argument("--stage8-catalogue",type=Path,required=True);ap.add_argument("--stage13-detections",type=Path,required=True)
    ap.add_argument("--config",type=Path);ap.add_argument("--output-dir",type=Path,required=True);args=ap.parse_args()
    cfg=json.loads((args.config or ROOT/"dart_stage14.json").read_text());out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(args.mfc_root.resolve()/"toolchain"));from mfc.viz.reader import assemble,discover_timesteps
    required=list(range(cfg["step_start"],cfg["step_stop"]+1,cfg["step_stride"]));available=discover_timesteps(str(args.case_dir.resolve()),"binary")
    missing=sorted(set(required)-set(available))
    if missing:ap.error(f"raw MFC sequence incomplete: missing {len(missing)}; first={missing[0]}")
    references={}
    with args.stage8_catalogue.open(newline="") as f:
        for row in csv.DictReader(f):references.setdefault(int(row["source_step"]),[]).append(row)
    snapshots={}
    for fi,step in enumerate(required):
        a=assemble(str(args.case_dir.resolve()),step,fmt="binary")
        xi=np.flatnonzero((a.x_cc>=cfg["analysis_xlim"][0])&(a.x_cc<=cfg["analysis_xlim"][1]));yi=np.flatnonzero((a.y_cc>=cfg["analysis_ylim"][0])&(a.y_cc<=cfg["analysis_ylim"][1]))
        xi=np.arange(max(0,xi[0]-3),min(a.x_cc.size,xi[-1]+4));yi=np.arange(max(0,yi[0]-3),min(a.y_cc.size,yi[-1]+4))
        x,y=a.x_cc[xi].copy(),a.y_cc[yi].copy();u=a.variables["vel1"][np.ix_(xi,yi)].copy();v=a.variables["vel2"][np.ix_(xi,yi)].copy()
        fields=derive_fields(x,y,u,v);fluid=stage8.geometry_fluid_mask(x,y)&(x[:,None]>=cfg["analysis_xlim"][0])&(x[:,None]<=cfg["analysis_xlim"][1])&(y[None,:]>=cfg["analysis_ylim"][0])&(y[None,:]<=cfg["analysis_ylim"][1])
        snapshots[fi]={"x":x,"y":y,"fluid":fluid,"reference":references.get(step,[]),"step":step,**fields};del a,u,v
    stage13=load_stage13(args.stage13_detections);calibration=list(range(1,31));holdout=list(range(31,61))
    selected={};sweep=[];holdout_metrics={};detections={"stage13":stage13}
    stage13_metrics,_=evaluate(lambda fi:stage13.get(fi,[]),snapshots,holdout,cfg["reference_match_radius"],cfg["close_pair_maximum_separation"])
    holdout_metrics["stage13"]=stage13_metrics
    for method in ["q","lci","omega_abs"]:
        parameters,rows=calibrate_method(method,snapshots,calibration,cfg);selected[method]=parameters;sweep.extend(rows)
        metrics,by=evaluate(lambda fi,m=method,p=parameters:baseline_detect(snapshots[fi],m,p),snapshots,holdout,cfg["reference_match_radius"],cfg["close_pair_maximum_separation"])
        holdout_metrics[method]=metrics;detections[method]=by
    consensus_by={};audit_nodes=[];agreements=[]
    for fi in range(61):
        per={m:(detections[m].get(fi,[]) if m=="stage13" else detections[m].get(fi,[])) for m in ["stage13","q","lci","omega_abs"]}
        consensus,audit=consensus_components(per,cfg["method_agreement_radius"]);consensus_by[fi]=consensus
        for node in audit:audit_nodes.append({"frame_index":fi,**node})
        for a,b in itertools.combinations(per,2):
            matched,na,nb=pairwise_agreement(per[a],per[b],cfg["method_agreement_radius"])
            agreements.append({"frame_index":fi,"method_a":a,"method_b":b,"matched":matched,"count_a":na,"count_b":nb})
    consensus_metrics,_=evaluate(lambda fi:consensus_by.get(fi,[]),snapshots,holdout,cfg["reference_match_radius"],cfg["close_pair_maximum_separation"])
    holdout_metrics["consensus"]=consensus_metrics
    for fi in cfg["comparison_frames"]:
        draw_comparison(out/f"stage14_comparison_{fi:04d}.png",snapshots[fi],
                        {m:(stage13.get(fi,[]) if m=="stage13" else detections[m].get(fi,[])) for m in ["stage13","q","lci","omega_abs"]},
                        f"Physical detector comparison: frame {fi}, step {snapshots[fi]['step']}")
    rng=np.random.default_rng(cfg["audit_seed"]);holdout_nodes=[n for n in audit_nodes if int(n["frame_index"]) in holdout]
    categories={"consensus":[n for n in holdout_nodes if int(n["support_count"])>=2],
                "stage13_only":[n for n in holdout_nodes if n["support_methods"]=="stage13"],
                "baseline_only":[n for n in holdout_nodes if int(n["support_count"])==1 and n["support_methods"]!="stage13"]}
    audit_key=[];audit_labels=[]
    for category in ["consensus","stage13_only","baseline_only"]:
        pool=categories[category];count=min(cfg["audit_samples_per_category"],len(pool))
        for node in (rng.choice(pool,size=count,replace=False).tolist() if count else []):
            audit_id=f"A{len(audit_key)+1:03d}";fi=int(node["frame_index"])
            draw_blind_crop(out/f"stage14_blind_{audit_id}.png",snapshots[fi],float(node["x"]),float(node["y"]),cfg["audit_crop_half_width"])
            audit_key.append({"audit_id":audit_id,"category":category,"frame_index":fi,"source_step":snapshots[fi]["step"],
                              "x_physical":node["x"],"y_physical":node["y"],"rotation_sign":node["sign"],
                              "support_methods":node["support_methods"],"support_count":node["support_count"]})
            audit_labels.append({"audit_id":audit_id,"is_vortex":"","corrected_dx":"","corrected_dy":"","confidence":"","annotator":"","notes":""})
    write_csv(out/"stage14_baseline_sweep.csv",sweep,list(sweep[0]));write_csv(out/"stage14_pairwise_agreement.csv",agreements,list(agreements[0]))
    write_csv(out/"stage14_blind_key.csv",audit_key,list(audit_key[0]));write_csv(out/"stage14_expert_labels.csv",audit_labels,list(audit_labels[0]))
    ranking=sorted(holdout_metrics,key=lambda m:(-holdout_metrics[m]["coverage"],holdout_metrics[m]["detection_to_reference_ratio"]))
    gates={"stage13_holdout_reproduced":"pass" if stage13_metrics["coverage"]>=.80 else "fail",
           "baseline_comparison_complete":"pass","consensus_candidate_control":"pass" if consensus_metrics["detection_to_reference_ratio"]<=cfg["maximum_consensus_detection_to_reference_ratio"] else "fail",
           "blind_audit_pack":"pass" if len(audit_key)>=cfg["minimum_audit_samples"] else "fail"}
    report={"schema_version":1,"status":"completed","created_at_utc":datetime.now(timezone.utc).isoformat(),
            "selected_baseline_configurations":selected,"holdout_metrics":holdout_metrics,"ranking_by_coverage":ranking,
            "blind_audit_samples":len(audit_key),"blind_audit_categories":{k:sum(r["category"]==k for r in audit_key) for k in categories},
            "gates":gates,"claim_gate":"comparative_holdout_complete_expert_labels_required",
            "limitations":["All automatic metrics use the non-exhaustive Stage 8 catalogue.","Q, swirling strength, and vorticity extrema are calibrated on frames 1-30 and tested on frames 31-60.",
                           "The consensus is cross-criterion agreement, not independent ground truth.","The blind audit labels must be completed before publication-level precision and recall are reported."]}
    (out/"stage14_report.json").write_text(json.dumps(report,indent=2)+"\n")
    print("STAGE14_STATUS=completed");print(f"STAGE14_STAGE13_COVERAGE={stage13_metrics['coverage']:.6f}")
    print(f"STAGE14_CONSENSUS_COVERAGE={consensus_metrics['coverage']:.6f}");print(f"STAGE14_CONSENSUS_RATIO={consensus_metrics['detection_to_reference_ratio']:.6f}")
    print(f"STAGE14_AUDIT_SAMPLES={len(audit_key)}");print(f"STAGE14_CLAIM_GATE={report['claim_gate']}");print(f"STAGE14_REPORT={out/'stage14_report.json'}")
    return 0


if __name__=="__main__":raise SystemExit(main())
