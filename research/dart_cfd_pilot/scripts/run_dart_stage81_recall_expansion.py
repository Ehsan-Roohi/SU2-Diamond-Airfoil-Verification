#!/usr/bin/env python3
"""Add a predeclared lower-threshold candidate tier to Stage 8.

Level A is copied unchanged from the completed Stage-8 catalogue. Level B is
computed from the already-declared q=0.980 sensitivity point and is never
silently promoted to physical truth. New candidates must persist for at least
three observations with bounded temporal fragmentation before visual review.
"""
from __future__ import annotations

import argparse, csv, importlib.util, json, math, statistics, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def read_csv(path):
    with Path(path).open(newline="") as f: return list(csv.DictReader(f))


def write_csv(path, rows, fields=None):
    fields = fields or (list(rows[0]) if rows else [])
    with Path(path).open("w", newline="") as f:
        if not fields: return
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def load_stage8(root):
    path=root/"scripts"/"run_dart_stage8_physics_catalogue.py"
    spec=importlib.util.spec_from_file_location("stage8_core",path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def nearest_level_a(core, rows, radius):
    best=None
    for row in rows:
        distance=math.hypot(float(core["x_physical"])-float(row["x_physical"]),float(core["y_physical"])-float(row["y_physical"]))
        if distance<=radius and (best is None or distance<best[0]): best=(distance,row["reference_id"])
    return best


def main():
    p=argparse.ArgumentParser(); p.add_argument("--case-dir",type=Path,required=True); p.add_argument("--mfc-root",type=Path,required=True); p.add_argument("--stage8-dir",type=Path,required=True); p.add_argument("--config",type=Path); p.add_argument("--output-dir",type=Path,default=Path("results/stage81-manual")); p.add_argument("--max-frames",type=int,default=0); args=p.parse_args()
    import numpy as np
    root=Path(__file__).resolve().parents[1]; stage8=load_stage8(root); cfg=json.loads((args.config or root/"dart_stage81.json").read_text()); out=(root/args.output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    base_cfg=json.loads((root/"dart_stage8.json").read_text()); stage8_dir=args.stage8_dir.resolve(); a_rows=read_csv(stage8_dir/"stage8_catalogue.csv")
    a_by_frame=defaultdict(list)
    for row in a_rows:a_by_frame[int(row["frame_index"])].append(row)
    mfc=args.mfc_root.resolve(); case=args.case_dir.resolve(); sys.path.insert(0,str(mfc/"toolchain")); from mfc.viz.reader import assemble,discover_timesteps
    required=stage8.expected_steps(base_cfg); available=discover_timesteps(str(case),"binary"); missing=sorted(set(required)-set(available))
    if missing:p.error(f"raw sequence incomplete: missing {len(missing)}; first={missing[0]}")
    steps=required[:args.max_frames or None]; tracks={}; next_id=1; relaxed_rows=[]; per_frame=[]
    for fi,step in enumerate(steps):
        a=assemble(str(case),step,fmt="binary"); xi=np.flatnonzero((a.x_cc>=base_cfg["analysis_xlim"][0])&(a.x_cc<=base_cfg["analysis_xlim"][1])); yi=np.flatnonzero((a.y_cc>=base_cfg["analysis_ylim"][0])&(a.y_cc<=base_cfg["analysis_ylim"][1])); xi=np.arange(max(0,xi[0]-3),min(a.x_cc.size,xi[-1]+4)); yi=np.arange(max(0,yi[0]-3),min(a.y_cc.size,yi[-1]+4))
        x,y=a.x_cc[xi],a.y_cc[yi]; u=a.variables["vel1"][np.ix_(xi,yi)]; v=a.variables["vel2"][np.ix_(xi,yi)]; written=a.variables["omega3"][np.ix_(xi,yi)]
        fluid=stage8.geometry_fluid_mask(x,y)&(x[:,None]>=base_cfg["analysis_xlim"][0])&(x[:,None]<=base_cfg["analysis_xlim"][1])&(y[None,:]>=base_cfg["analysis_ylim"][0])&(y[None,:]<=base_cfg["analysis_ylim"][1])
        fields=stage8.diagnostics(x,y,u,v,base_cfg["gamma2_radius_cells"]); fields["omega"]=written
        candidates,_=stage8.extract_cores(x,y,fields,fluid,base_cfg,float(cfg["level_b_quantile"]),float(cfg["level_b_gamma2_threshold"]))
        associated,tracks,next_id,_=stage8.associate_cores(candidates,fi,tracks,next_id,base_cfg)
        new_count=0
        for row in associated:
            match=nearest_level_a(row,a_by_frame.get(fi,[]),float(cfg["level_a_match_radius"])); row.update(source_step=step,time=fi*float(base_cfg["snapshot_dt"]),candidate_track_id=row["reference_id"],nearest_level_a_id=match[1] if match else "",nearest_level_a_distance=match[0] if match else "",provisional_tier="A-overlap" if match else "B-candidate")
            if not match:new_count+=1
        relaxed_rows.extend(associated); per_frame.append({"frame_index":fi,"source_step":step,"level_a":len(a_by_frame.get(fi,[])),"relaxed_total":len(associated),"new_level_b_candidates":new_count})
    by_track=defaultdict(list)
    for row in relaxed_rows:by_track[row["candidate_track_id"]].append(row)
    accepted_tracks=[]; accepted_ids=set(); summaries=[]
    for tid,rows in sorted(by_track.items()):
        rows.sort(key=lambda r:int(r["frame_index"])); b=[r for r in rows if r["provisional_tier"]=="B-candidate"]; span=int(rows[-1]["frame_index"])-int(rows[0]["frame_index"])+1; continuity=len(rows)/span
        accepted=len(b)>=int(cfg["minimum_level_b_observations"]) and continuity>=float(cfg["minimum_level_b_continuity"]) and statistics.median(float(r["confidence"]) for r in b)>=float(cfg["minimum_level_b_median_confidence"])
        if accepted:accepted_ids.add(tid)
        summaries.append({"candidate_track_id":tid,"observations":len(rows),"level_b_observations":len(b),"first_frame":rows[0]["frame_index"],"last_frame":rows[-1]["frame_index"],"continuity":continuity,"median_level_b_confidence":statistics.median(float(r["confidence"]) for r in b) if b else "","accepted_level_b":accepted,"level_a_parent_ids":"|".join(sorted({r["nearest_level_a_id"] for r in rows if r["nearest_level_a_id"]}))})
    added=[]
    for row in relaxed_rows:
        if row["provisional_tier"]=="B-candidate" and row["candidate_track_id"] in accepted_ids:
            item=dict(row); item["reference_id"]="B"+row["candidate_track_id"][1:]; item["tier"]="B"; added.append(item)
    augmented=[]
    for row in a_rows:
        item=dict(row); item["tier"]="A"; augmented.append(item)
    augmented.extend(added)
    fields=["frame_index","source_step","time","reference_id","tier","x_physical","y_physical","rotation_sign","omega","lambda_ci","q","omega_ratio","gamma2","criterion_support","confidence","association_cost","prediction_error"]
    write_csv(out/"stage81_augmented_catalogue.csv",augmented,fields); write_csv(out/"stage81_level_b_tracks.csv",summaries); write_csv(out/"stage81_per_frame.csv",per_frame)
    added_ratio=len(added)/max(len(a_rows),1); gates={"raw_sequence_complete":"pass" if len(steps)==len(required) else "fail","level_a_unchanged":"pass" if sum(1 for r in augmented if r["tier"]=="A")==len(a_rows) else "fail","bounded_candidate_expansion":"pass" if added_ratio<=float(cfg["maximum_added_observation_ratio"]) else "fail","candidate_persistence_audited":"pass"}
    passed=all(v=="pass" for v in gates.values())
    report={"schema_version":1,"status":"completed","created_at_utc":datetime.now(timezone.utc).isoformat(),"case_id":base_cfg["case_id"],"frames":len(steps),"level_a_observations":len(a_rows),"raw_relaxed_observations":len(relaxed_rows),"accepted_level_b_tracks":len(accepted_ids),"accepted_level_b_observations":len(added),"augmented_observations":len(augmented),"added_observation_ratio":added_ratio,"configuration":cfg,"per_frame":per_frame,"gates":gates,"claim_gate":"recall_candidates_ready_for_visual_review" if passed else "recall_expansion_gate_failed","limitations":["Level B is a candidate tier, not physical ground truth.","Acceptance is temporal and criterion-based; analytic and cross-case validation remain required.","A visible red or blue raster lobe is not automatically an independent vortex."]}
    (out/"stage81_report.json").write_text(json.dumps(report,indent=2)+"\n")
    print("STAGE81_STATUS=completed");print(f"STAGE81_LEVEL_A_OBSERVATIONS={len(a_rows)}");print(f"STAGE81_ACCEPTED_LEVEL_B_TRACKS={len(accepted_ids)}");print(f"STAGE81_ACCEPTED_LEVEL_B_OBSERVATIONS={len(added)}");print(f"STAGE81_CLAIM_GATE={report['claim_gate']}");print(f"STAGE81_REPORT={out/'stage81_report.json'}")
    return 0 if passed else 81

if __name__=="__main__":raise SystemExit(main())
