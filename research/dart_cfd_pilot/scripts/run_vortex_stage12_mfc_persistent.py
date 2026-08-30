#!/usr/bin/env python3
"""Build a high-capacity, temporally persistent vortex catalogue from raw MFC fields."""
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
from scipy.optimize import linear_sum_assignment


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
    for i,d in sorted(enumerate(detections),key=lambda z:-float(z[1].get("score",0))):
        sign=int(d.get("sign",d.get("rotation_sign",0)))
        candidates=[j for j in remaining if int(reference[j]["rotation_sign"])==sign]
        if not candidates:continue
        j=min(candidates,key=lambda k:math.hypot(float(d.get("x",d.get("x_physical")))-float(reference[k]["x_physical"]),
                                                float(d.get("y",d.get("y_physical")))-float(reference[k]["y_physical"])))
        distance=math.hypot(float(d.get("x",d.get("x_physical")))-float(reference[j]["x_physical"]),
                            float(d.get("y",d.get("y_physical")))-float(reference[j]["y_physical"]))
        if distance<=radius:matched.append((i,j,distance));remaining.remove(j)
    return matched


def associate_tracks(rows, maximum_gap, maximum_displacement_per_frame):
    """Assign sign-consistent tracks with one-to-one gated Hungarian association."""
    by_frame={}
    for i,row in enumerate(rows):by_frame.setdefault(int(row["frame_index"]),[]).append(i)
    tracks={};active={};next_id=1
    for frame in sorted(by_frame):
        indices=by_frame[frame];active={tid:last for tid,last in active.items()
                                       if frame-int(rows[last]["frame_index"])<=maximum_gap+1}
        tids=sorted(active);cost=np.full((len(tids),len(indices)),1e9)
        for a,tid in enumerate(tids):
            previous=rows[active[tid]];gap=frame-int(previous["frame_index"])
            gate=maximum_displacement_per_frame*gap
            for b,index in enumerate(indices):
                current=rows[index]
                if int(previous["rotation_sign"])!=int(current["rotation_sign"]):continue
                distance=math.hypot(float(previous["x_physical"])-float(current["x_physical"]),
                                    float(previous["y_physical"])-float(current["y_physical"]))
                if distance<=gate:cost[a,b]=distance
        used=set()
        if cost.size:
            ai,bi=linear_sum_assignment(cost)
            for a,b in zip(ai,bi):
                if cost[a,b]>=1e8:continue
                tid=tids[a];index=indices[b];tracks[tid].append(index);active[tid]=index;used.add(index)
        for index in indices:
            if index in used:continue
            tid=next_id;next_id+=1;tracks[tid]=[index];active[tid]=index
    for tid,indices in tracks.items():
        for index in indices:rows[index]["track_id"]=f"T{tid:04d}"
    return tracks


def summarize_tracks(rows, tracks, minimum_observations, minimum_continuity):
    summaries=[]
    for tid,indices in sorted(tracks.items()):
        frames=[int(rows[i]["frame_index"]) for i in indices];span=max(frames)-min(frames)+1
        continuity=len(indices)/span
        path=sum(math.hypot(float(rows[b]["x_physical"])-float(rows[a]["x_physical"]),
                            float(rows[b]["y_physical"])-float(rows[a]["y_physical"]))
                 for a,b in zip(indices,indices[1:]))
        persistent=len(indices)>=minimum_observations and continuity>=minimum_continuity
        summary={"track_id":f"T{tid:04d}","rotation_sign":int(rows[indices[0]]["rotation_sign"]),
                 "observations":len(indices),"first_frame":min(frames),"last_frame":max(frames),
                 "span_frames":span,"continuity":continuity,"path_length":path,
                 "median_score":float(np.median([float(rows[i]["score"]) for i in indices])),
                 "persistent":persistent}
        summaries.append(summary)
        for i in indices:
            rows[i]["track_observations"]=len(indices);rows[i]["track_continuity"]=continuity
            rows[i]["persistent"]=persistent
    return summaries


def draw_overlay(path,x,y,omega,fluid,raw,persistent,reference,title):
    import matplotlib.pyplot as plt
    masked=np.where(fluid,omega,np.nan);lim=float(np.nanpercentile(np.abs(masked),99.5));lim=max(lim,1e-8)
    fig,ax=plt.subplots(figsize=(9.2,7.3),constrained_layout=True)
    cf=ax.contourf(x,y,masked.T,levels=np.linspace(-lim,lim,101),cmap="RdBu_r",extend="both")
    if raw:
        ax.scatter([float(d["x_physical"]) for d in raw],[float(d["y_physical"]) for d in raw],
                   s=13,c="#00e6ff",alpha=.55,label="raw candidates")
    for k,d in enumerate(persistent):
        color="#ffd400" if int(d["rotation_sign"])>0 else "#7cff00"
        ax.plot(float(d["x_physical"]),float(d["y_physical"]),"o",ms=9,mfc="none",mec=color,mew=1.9,
                label=("persistent tracks" if k==0 else None))
    if reference:
        ax.scatter([float(r["x_physical"]) for r in reference],[float(r["y_physical"]) for r in reference],
                   marker="+",s=48,c="black",linewidths=1.35,label="Stage 8 physics catalogue")
    ax.set(xlabel="x",ylabel="y",title=title);ax.set_aspect("equal");ax.legend(loc="upper left",framealpha=.88)
    c=fig.colorbar(cf,ax=ax);c.set_label(r"Spanwise vorticity $\omega_z$")
    fig.savefig(path,dpi=240,bbox_inches="tight");plt.close(fig)


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--case-dir",type=Path,required=True);ap.add_argument("--mfc-root",type=Path,required=True)
    ap.add_argument("--stage8-catalogue",type=Path);ap.add_argument("--config",type=Path);ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--max-frames",type=int,default=0);args=ap.parse_args()
    cfg=json.loads((args.config or ROOT/"dart_stage12.json").read_text());out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    stage10e.BASE_CFG["maximum_detections"]=int(cfg["maximum_detections_per_frame"])
    sys.path.insert(0,str(args.mfc_root.resolve()/"toolchain"));from mfc.viz.reader import assemble,discover_timesteps
    required=list(range(cfg["step_start"],cfg["step_stop"]+1,cfg["step_stride"]));available=discover_timesteps(str(args.case_dir.resolve()),"binary")
    missing=sorted(set(required)-set(available))
    if missing:ap.error(f"raw MFC sequence incomplete: missing {len(missing)}; first={missing[0]}")
    steps=required[:args.max_frames or None];references={}
    if args.stage8_catalogue and args.stage8_catalogue.is_file():
        with args.stage8_catalogue.open(newline="") as f:
            for row in csv.DictReader(f):references.setdefault(int(row["source_step"]),[]).append(row)
    rows=[];diagnostics=[];snapshots={};selected={0,len(steps)//2,len(steps)-1}
    for fi,step in enumerate(steps):
        a=assemble(str(args.case_dir.resolve()),step,fmt="binary");absent=sorted({"vel1","vel2","omega3"}-set(a.variables))
        if absent:raise RuntimeError(f"step {step} lacks {absent}")
        xi=np.flatnonzero((a.x_cc>=cfg["analysis_xlim"][0])&(a.x_cc<=cfg["analysis_xlim"][1]));yi=np.flatnonzero((a.y_cc>=cfg["analysis_ylim"][0])&(a.y_cc<=cfg["analysis_ylim"][1]))
        xi=np.arange(max(0,xi[0]-3),min(a.x_cc.size,xi[-1]+4));yi=np.arange(max(0,yi[0]-3),min(a.y_cc.size,yi[-1]+4))
        x,y=a.x_cc[xi],a.y_cc[yi];u=a.variables["vel1"][np.ix_(xi,yi)];v=a.variables["vel2"][np.ix_(xi,yi)];omega=a.variables["omega3"][np.ix_(xi,yi)]
        fluid=stage8.geometry_fluid_mask(x,y)&(x[:,None]>=cfg["analysis_xlim"][0])&(x[:,None]<=cfg["analysis_xlim"][1])&(y[None,:]>=cfg["analysis_ylim"][0])&(y[None,:]<=cfg["analysis_ylim"][1])
        detected,fit_rows=stage10e.detect_deblended(x,y,u,v,DEBLEND_CFG,return_diagnostics=True)
        detected=[d for d in detected if fluid[np.argmin(abs(x-d["x"])),np.argmin(abs(y-d["y"]))]]
        ref=references.get(step,[]);matches=match_reference(detected,ref,cfg["reference_match_radius"]);matched={i:(j,dist) for i,j,dist in matches}
        frame_rows=[]
        for i,d in enumerate(detected):
            jdist=matched.get(i);row={"frame_index":fi,"source_step":step,"time":fi*cfg["snapshot_dt"],"detection_id":f"F{fi:03d}D{i:03d}",
                "x_physical":d["x"],"y_physical":d["y"],"rotation_sign":d["sign"],"radius":d["radius"],"score":d["score"],"lambda_ci":d["lambda_ci"],
                "gamma2":d["gamma2"],"omega_ratio":d["omega_ratio"],"deblended":bool(d.get("deblended",False)),
                "matched_stage8_raw":jdist is not None,"stage8_reference_id_raw":ref[jdist[0]]["reference_id"] if jdist else "","reference_distance_raw":jdist[1] if jdist else ""}
            rows.append(row);frame_rows.append(row)
        for d in fit_rows:diagnostics.append({"frame_index":fi,"source_step":step,**d})
        if fi in selected:snapshots[fi]=(x,y,omega,fluid,frame_rows,ref,step)
    tracks=associate_tracks(rows,int(cfg["maximum_track_gap_frames"]),float(cfg["maximum_track_displacement_per_frame"]))
    track_rows=summarize_tracks(rows,tracks,int(cfg["minimum_track_observations"]),float(cfg["minimum_track_continuity"]))
    persistent=[r for r in rows if r["persistent"]]
    for row in rows:row["matched_stage8_persistent"]=False;row["stage8_reference_id_persistent"]="";row["reference_distance_persistent"]=""
    per_frame=[]
    for fi,step in enumerate(steps):
        raw=[r for r in rows if int(r["frame_index"])==fi];kept=[r for r in raw if r["persistent"]];ref=references.get(step,[])
        matches=match_reference(kept,ref,cfg["reference_match_radius"])
        for i,j,distance in matches:
            kept[i]["matched_stage8_persistent"]=True;kept[i]["stage8_reference_id_persistent"]=ref[j]["reference_id"];kept[i]["reference_distance_persistent"]=distance
        per_frame.append({"frame_index":fi,"source_step":step,"raw_detections":len(raw),"persistent_detections":len(kept),
                          "stage8_reference":len(ref),"raw_matches":sum(bool(r["matched_stage8_raw"]) for r in raw),
                          "persistent_matches":len(matches),"raw_additional":len(raw)-sum(bool(r["matched_stage8_raw"]) for r in raw),
                          "persistent_additional":len(kept)-len(matches)})
    for fi,(x,y,omega,fluid,raw,ref,step) in snapshots.items():
        kept=[r for r in raw if r["persistent"]]
        draw_overlay(out/f"stage12_frame_{fi:04d}.png",x,y,omega,fluid,raw,kept,ref,
                     f"MFC/ILES Stage 12: frame {fi}, step {step}")
    fields=["frame_index","source_step","time","detection_id","track_id","track_observations","track_continuity","persistent",
            "x_physical","y_physical","rotation_sign","radius","score","lambda_ci","gamma2","omega_ratio","deblended",
            "matched_stage8_raw","stage8_reference_id_raw","reference_distance_raw","matched_stage8_persistent",
            "stage8_reference_id_persistent","reference_distance_persistent"]
    write_csv(out/"stage12_detections.csv",rows,fields);write_csv(out/"stage12_tracks.csv",track_rows,list(track_rows[0]))
    write_csv(out/"stage12_per_frame.csv",per_frame,list(per_frame[0]))
    diagnostic_fields=["frame_index","source_step","input_x","input_y","rotation_sign","input_score","fit_available","split",
                       "bic_gain","improvement","amplitude_ratio","separation","normalized_separation"]
    write_csv(out/"stage12_fit_diagnostics.csv",diagnostics,diagnostic_fields)
    total_ref=sum(r["stage8_reference"] for r in per_frame);raw_matches=sum(r["raw_matches"] for r in per_frame)
    persistent_matches=sum(r["persistent_matches"] for r in per_frame);raw_count=len(rows);persistent_count=len(persistent)
    raw_coverage=raw_matches/max(total_ref,1);persistent_coverage=persistent_matches/max(total_ref,1)
    report={"schema_version":1,"status":"completed","created_at_utc":datetime.now(timezone.utc).isoformat(),"frames":len(steps),
            "configuration":{k:cfg[k] for k in ["maximum_detections_per_frame","maximum_track_gap_frames","maximum_track_displacement_per_frame","minimum_track_observations","minimum_track_continuity"]},
            "raw_detections":raw_count,"persistent_detections":persistent_count,"tracks":len(tracks),
            "persistent_tracks":sum(bool(r["persistent"]) for r in track_rows),"deblended_detections":sum(bool(r["deblended"]) for r in rows),
            "fit_attempts":len(diagnostics),"fit_available":sum(bool(r["fit_available"]) for r in diagnostics),"fit_splits":sum(bool(r["split"]) for r in diagnostics),
            "stage8_reference_rows":total_ref,"raw_stage8_matches":raw_matches,"persistent_stage8_matches":persistent_matches,
            "raw_stage8_coverage":raw_coverage,"persistent_stage8_coverage":persistent_coverage,
            "raw_detection_to_reference_ratio":raw_count/max(total_ref,1),"persistent_detection_to_reference_ratio":persistent_count/max(total_ref,1),
            "gates":{"raw_sequence_complete":"pass","finite_output":"pass" if all(np.isfinite(float(r["x_physical"])) for r in rows) else "fail",
                     "raw_stage8_coverage":"pass" if raw_coverage>=cfg["minimum_raw_stage8_coverage"] else "fail",
                     "persistent_stage8_coverage":"pass" if persistent_coverage>=cfg["minimum_persistent_stage8_coverage"] else "fail",
                     "raw_candidate_growth":"pass" if raw_count/max(total_ref,1)<=cfg["maximum_raw_detection_to_reference_ratio"] else "fail",
                     "persistent_candidate_growth":"pass" if persistent_count/max(total_ref,1)<=cfg["maximum_persistent_detection_to_reference_ratio"] else "fail"},
            "claim_gate":"persistent_real_field_catalogue_requires_independent_visual_audit",
            "limitations":["Stage 8 is a physics baseline, not exhaustive ground truth.","Persistence is a temporal quality filter, not proof that every retained center is a vortex.",
                           "The higher per-frame cap is predeclared and both raw and persistent catalogues are retained.","This is two-dimensional core detection, not 3-D vortex-tube segmentation."]}
    (out/"stage12_report.json").write_text(json.dumps(report,indent=2)+"\n")
    print("STAGE12_STATUS=completed");print(f"STAGE12_RAW_DETECTIONS={raw_count}");print(f"STAGE12_PERSISTENT_DETECTIONS={persistent_count}")
    print(f"STAGE12_PERSISTENT_TRACKS={report['persistent_tracks']}");print(f"STAGE12_RAW_COVERAGE={raw_coverage:.6f}")
    print(f"STAGE12_PERSISTENT_COVERAGE={persistent_coverage:.6f}");print(f"STAGE12_CLAIM_GATE={report['claim_gate']}")
    print(f"STAGE12_REPORT={out/'stage12_report.json'}");return 0


if __name__=="__main__":raise SystemExit(main())
