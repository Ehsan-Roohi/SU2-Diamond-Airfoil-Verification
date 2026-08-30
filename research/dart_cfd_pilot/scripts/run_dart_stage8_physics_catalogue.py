#!/usr/bin/env python3
"""Build a physics-consistent, uncertainty-aware 2-D vortex catalogue.

Stage 8 reuses completed Stage-5 MFC binary snapshots.  It forms an ensemble
of signed vorticity, swirling strength, Q, Omega ratio, and Graftieaux Gamma2,
then tracks the resulting cores with rotation-sign preservation, a
constant-velocity predictor, bounded gap closing, and strength continuity.
No rendered image or DART output enters the reference construction.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

AIRFOIL_HALF_HEIGHT = 0.0702704174


def expected_steps(config):
    return list(range(int(config["step_start"]), int(config["step_stop"]) + 1, int(config["step_stride"])))


def geometry_fluid_mask(x, y):
    import numpy as np
    dx, dy = float(np.min(np.diff(x))), float(np.min(np.diff(y)))
    pad = 3.0 * max(dx, dy)
    xx, yy = x[:, None], y[None, :]
    clipped = np.clip(xx, 0.0, 1.0)
    half = AIRFOIL_HALF_HEIGHT * (1.0 - np.abs(2.0 * clipped - 1.0))
    return ~((xx >= -pad) & (xx <= 1.0 + pad) & (np.abs(yy) <= half + pad))


def diagnostics(x, y, u, v, gamma_radius=2):
    import numpy as np
    du_dx, du_dy = np.gradient(u, x, y, edge_order=2)
    dv_dx, dv_dy = np.gradient(v, x, y, edge_order=2)
    omega = dv_dx - du_dy
    trace = du_dx + dv_dy
    determinant = du_dx * dv_dy - du_dy * dv_dx
    discriminant = trace * trace - 4.0 * determinant
    lambda_ci = 0.5 * np.sqrt(np.maximum(-discriminant, 0.0))
    q_criterion = -0.5 * (du_dx * du_dx + dv_dy * dv_dy + 2.0 * du_dy * dv_dx)
    strain2 = du_dx * du_dx + dv_dy * dv_dy + 0.5 * (du_dy + dv_dx) ** 2
    rotation2 = 0.5 * omega * omega
    epsilon = 1.0e-12 * max(float(np.nanmax(strain2 + rotation2)), 1.0)
    omega_ratio = rotation2 / (rotation2 + strain2 + epsilon)
    gamma2 = graftieaux_gamma2(x, y, u, v, int(gamma_radius))
    return {"omega": omega, "lambda_ci": lambda_ci, "q": q_criterion, "omega_ratio": omega_ratio, "gamma2": gamma2}


def graftieaux_gamma2(x, y, u, v, radius):
    """Vectorized Gamma2 with a circular physical-space neighbourhood."""
    import numpy as np
    gamma = np.zeros_like(u, dtype=float)
    count = np.zeros_like(u, dtype=float)
    # Local mean over the same circular stencil, computed without scipy.
    ubar = np.zeros_like(u, dtype=float); vbar = np.zeros_like(v, dtype=float); nbar = np.zeros_like(u, dtype=float)
    offsets = [(di, dj) for di in range(-radius, radius + 1) for dj in range(-radius, radius + 1) if di * di + dj * dj <= radius * radius]
    for di, dj in offsets:
        src_i = slice(max(0, -di), min(u.shape[0], u.shape[0] - di)); dst_i = slice(max(0, di), min(u.shape[0], u.shape[0] + di))
        src_j = slice(max(0, -dj), min(u.shape[1], u.shape[1] - dj)); dst_j = slice(max(0, dj), min(u.shape[1], u.shape[1] + dj))
        ubar[dst_i, dst_j] += u[src_i, src_j]; vbar[dst_i, dst_j] += v[src_i, src_j]; nbar[dst_i, dst_j] += 1.0
    ubar /= np.maximum(nbar, 1.0); vbar /= np.maximum(nbar, 1.0)
    dx0, dy0 = float(np.median(np.diff(x))), float(np.median(np.diff(y)))
    eps = 1.0e-14
    for di, dj in offsets:
        if di == 0 and dj == 0: continue
        src_i = slice(max(0, -di), min(u.shape[0], u.shape[0] - di)); dst_i = slice(max(0, di), min(u.shape[0], u.shape[0] + di))
        src_j = slice(max(0, -dj), min(u.shape[1], u.shape[1] - dj)); dst_j = slice(max(0, dj), min(u.shape[1], u.shape[1] + dj))
        du = u[src_i, src_j] - ubar[dst_i, dst_j]; dv = v[src_i, src_j] - vbar[dst_i, dst_j]
        rx, ry = -di * dx0, -dj * dy0
        denom = math.hypot(rx, ry) * np.sqrt(du * du + dv * dv) + eps
        gamma[dst_i, dst_j] += (rx * dv - ry * du) / denom
        count[dst_i, dst_j] += 1.0
    return gamma / np.maximum(count, 1.0)


def quantile_threshold(values, mask, q, positive=False):
    import numpy as np
    selected = values[mask & np.isfinite(values)]
    if positive: selected = selected[selected > 0.0]
    return float(np.quantile(selected, q)) if selected.size else math.inf


def extract_cores(x, y, fields, fluid, config, quantile=None, gamma_threshold=None):
    import numpy as np
    q = float(config["criterion_quantile"] if quantile is None else quantile)
    gt = float(config["minimum_absolute_gamma2"] if gamma_threshold is None else gamma_threshold)
    omega, lci, qc, ratio, gamma = (fields[k] for k in ("omega", "lambda_ci", "q", "omega_ratio", "gamma2"))
    rotating = fluid & np.isfinite(omega) & (lci > 0.0)
    thresholds = {
        "absolute_vorticity": quantile_threshold(np.abs(omega), rotating, q),
        "lambda_ci": quantile_threshold(lci, rotating, q, positive=True),
        "q": quantile_threshold(qc, rotating, q, positive=True),
        "omega_ratio": float(config["minimum_omega_ratio"]),
        "absolute_gamma2": gt,
    }
    criteria = [np.abs(omega) >= thresholds["absolute_vorticity"], lci >= thresholds["lambda_ci"], qc >= thresholds["q"], ratio >= thresholds["omega_ratio"], np.abs(gamma) >= gt]
    support = sum(item.astype(np.int8) for item in criteria)
    candidate = rotating & (support >= int(config["minimum_criterion_support"])) & (criteria[1] | criteria[2]) & criteria[4]
    indices = np.argwhere(candidate)
    if not indices.size: return [], thresholds
    scales = [max(thresholds["absolute_vorticity"], 1e-15), max(thresholds["lambda_ci"], 1e-15), max(thresholds["q"], 1e-15)]
    score = support / 5.0 + 0.10 * np.clip(np.abs(omega) / scales[0], 0, 5) + 0.10 * np.clip(lci / scales[1], 0, 5) + 0.05 * np.clip(np.maximum(qc, 0) / scales[2], 0, 5) + 0.05 * np.abs(gamma)
    order = sorted(indices.tolist(), key=lambda ij: (-float(score[tuple(ij)]), int(ij[0]), int(ij[1])))
    separation = float(config["minimum_core_separation"]); maximum = int(config["maximum_cores_per_frame"])
    accepted=[]
    for i,j in order:
        xp,yp=float(x[i]),float(y[j])
        if any(math.hypot(xp-r["x_physical"],yp-r["y_physical"]) < separation for r in accepted): continue
        accepted.append({"x_physical":xp,"y_physical":yp,"rotation_sign":1 if omega[i,j]>=0 else -1,"omega":float(omega[i,j]),"lambda_ci":float(lci[i,j]),"q":float(qc[i,j]),"omega_ratio":float(ratio[i,j]),"gamma2":float(gamma[i,j]),"criterion_support":int(support[i,j]),"confidence":float(min(score[i,j]/2.0,1.0))})
        if len(accepted)>=maximum: break
    return accepted, thresholds


def predicted_position(history, frame_index):
    if len(history) < 2: return float(history[-1]["x_physical"]), float(history[-1]["y_physical"])
    a,b=history[-2],history[-1]; dt=max(int(b["frame_index"])-int(a["frame_index"]),1); ahead=frame_index-int(b["frame_index"])
    return float(b["x_physical"])+ahead*(float(b["x_physical"])-float(a["x_physical"]))/dt, float(b["y_physical"])+ahead*(float(b["y_physical"])-float(a["y_physical"]))/dt


def associate_cores(cores, frame_index, tracks, next_id, config):
    max_gap=int(config["maximum_track_gap_frames"]); max_d=float(config["maximum_reference_displacement"]); strength_weight=float(config["strength_continuity_weight"])
    candidates=[]
    for ci,core in enumerate(cores):
        for tid,hist in tracks.items():
            last=hist[-1]; gap=frame_index-int(last["frame_index"])
            if gap<1 or gap>max_gap+1 or int(core["rotation_sign"])!=int(last["rotation_sign"]): continue
            px,py=predicted_position(hist,frame_index); distance=math.hypot(float(core["x_physical"])-px,float(core["y_physical"])-py)
            gate=max_d*math.sqrt(gap)
            if distance>gate: continue
            ratio=abs(math.log((abs(float(core["omega"]))+1e-12)/(abs(float(last["omega"]))+1e-12)))
            candidates.append((distance/gate+strength_weight*ratio,ci,tid,distance))
    assignments={}; used_c=set(); used_t=set()
    for cost,ci,tid,distance in sorted(candidates):
        if ci in used_c or tid in used_t: continue
        assignments[ci]=(tid,cost,distance); used_c.add(ci); used_t.add(tid)
    rows=[]; events=[]
    for ci,core in enumerate(cores):
        if ci in assignments: tid,cost,distance=assignments[ci]
        else: tid,cost,distance=next_id,0.0,0.0; next_id+=1; tracks[tid]=[]
        row=dict(core,reference_id=f"P{tid:05d}",frame_index=frame_index,association_cost=cost,prediction_error=distance)
        tracks.setdefault(tid,[]).append(row); rows.append(row)
    # Record ambiguous neighbourhoods as audit events, without forcing topology.
    by_core=defaultdict(list); by_track=defaultdict(list)
    for cost,ci,tid,distance in candidates:
        if cost<=1.0: by_core[ci].append(tid); by_track[tid].append(ci)
    for ci,tids in by_core.items():
        if len(set(tids))>1: events.append({"frame_index":frame_index,"event":"possible_merge","object_ids":"|".join(f"P{x:05d}" for x in sorted(set(tids)))})
    for tid,cis in by_track.items():
        if len(set(cis))>1: events.append({"frame_index":frame_index,"event":"possible_split","object_ids":f"P{tid:05d}"})
    return rows,tracks,next_id,events


def track_summary(tracks, dt):
    output=[]
    for tid,hist in sorted(tracks.items()):
        hist=sorted(hist,key=lambda r:r["frame_index"]); span=hist[-1]["frame_index"]-hist[0]["frame_index"]+1
        output.append({"reference_id":f"P{tid:05d}","observations":len(hist),"first_frame":hist[0]["frame_index"],"last_frame":hist[-1]["frame_index"],"lifetime":(span-1)*dt,"continuity":len(hist)/span,"displacement":math.hypot(hist[-1]["x_physical"]-hist[0]["x_physical"],hist[-1]["y_physical"]-hist[0]["y_physical"]),"rotation_sign":hist[0]["rotation_sign"],"median_confidence":statistics.median(r["confidence"] for r in hist),"median_prediction_error":statistics.median(r["prediction_error"] for r in hist[1:]) if len(hist)>1 else 0.0})
    return output


def write_csv(path, rows, fields=None):
    if fields is None: fields=list(rows[0]) if rows else []
    with path.open("w",newline="") as f:
        if not fields: return
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def git_head(path):
    p=subprocess.run(["git","rev-parse","HEAD"],cwd=path,text=True,capture_output=True)
    return p.stdout.strip() if p.returncode==0 else None


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--case-dir",type=Path,required=True); parser.add_argument("--mfc-root",type=Path,required=True); parser.add_argument("--config",type=Path); parser.add_argument("--output-dir",type=Path,default=Path("results/stage8-manual")); parser.add_argument("--max-frames",type=int,default=0); args=parser.parse_args()
    import numpy as np
    root=Path(__file__).resolve().parents[1]; cfg=json.loads((args.config or root/"dart_stage8.json").read_text()); out=(root/args.output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    case=args.case_dir.resolve(); mfc=args.mfc_root.resolve(); sys.path.insert(0,str(mfc/"toolchain")); from mfc.viz.reader import assemble,discover_timesteps
    required=expected_steps(cfg); available=discover_timesteps(str(case),"binary"); missing=sorted(set(required)-set(available))
    if missing: parser.error(f"raw MFC sequence incomplete: missing {len(missing)}; first={missing[0]}")
    steps=required[:args.max_frames or None]; all_rows=[]; tracks={}; next_id=1; events=[]; per_frame=[]; sensitivity=[]
    for fi,step in enumerate(steps):
        a=assemble(str(case),step,fmt="binary"); absent=sorted({"vel1","vel2","omega3"}-set(a.variables));
        if absent: raise RuntimeError(f"step {step} lacks {absent}")
        xi=np.flatnonzero((a.x_cc>=cfg["analysis_xlim"][0])&(a.x_cc<=cfg["analysis_xlim"][1])); yi=np.flatnonzero((a.y_cc>=cfg["analysis_ylim"][0])&(a.y_cc<=cfg["analysis_ylim"][1])); xi=np.arange(max(0,xi[0]-3),min(a.x_cc.size,xi[-1]+4)); yi=np.arange(max(0,yi[0]-3),min(a.y_cc.size,yi[-1]+4))
        x,y=a.x_cc[xi],a.y_cc[yi]; u=a.variables["vel1"][np.ix_(xi,yi)]; v=a.variables["vel2"][np.ix_(xi,yi)]; written=a.variables["omega3"][np.ix_(xi,yi)]
        fluid=geometry_fluid_mask(x,y)&(x[:,None]>=cfg["analysis_xlim"][0])&(x[:,None]<=cfg["analysis_xlim"][1])&(y[None,:]>=cfg["analysis_ylim"][0])&(y[None,:]<=cfg["analysis_ylim"][1])
        if not (np.isfinite(u[fluid]).all() and np.isfinite(v[fluid]).all() and np.isfinite(written[fluid]).all()): raise RuntimeError(f"non-finite field at {step}")
        fields=diagnostics(x,y,u,v,cfg["gamma2_radius_cells"]); derived=fields["omega"]; valid=fluid&np.isfinite(derived); corr=float(np.corrcoef(written[valid][::8],derived[valid][::8])[0,1]) if valid.sum()>80 else None
        fields["omega"]=written
        cores,thresholds=extract_cores(x,y,fields,fluid,cfg); associated,tracks,next_id,frame_events=associate_cores(cores,fi,tracks,next_id,cfg)
        for row in associated:
            row.update(source_step=step, time=fi * float(cfg["snapshot_dt"]))
        all_rows.extend(associated)
        events.extend(frame_events)
        variants={}
        for q in cfg["sensitivity_quantiles"]:
            for gt in cfg["sensitivity_gamma2_thresholds"]:
                key=f"q={q:.3f},gamma2={gt:.2f}"; variant,_=extract_cores(x,y,fields,fluid,cfg,q,gt); variants[key]=len(variant); sensitivity.append({"frame_index":fi,"source_step":step,"quantile":q,"gamma2_threshold":gt,"cores":len(variant)})
        per_frame.append({"frame_index":fi,"source_step":step,"time":fi*float(cfg["snapshot_dt"]),"cores":len(cores),"vorticity_correlation":corr,"thresholds":thresholds,"sensitivity_counts":variants})
    summaries=track_summary(tracks,float(cfg["snapshot_dt"])); persistent=[r for r in summaries if r["observations"]>=cfg["minimum_track_observations"] and r["continuity"]>=cfg["minimum_track_continuity"]]
    fields=["frame_index","source_step","time","reference_id","x_physical","y_physical","rotation_sign","omega","lambda_ci","q","omega_ratio","gamma2","criterion_support","confidence","association_cost","prediction_error"]
    write_csv(out/"stage8_catalogue.csv",all_rows,fields); write_csv(out/"stage8_tracks.csv",summaries); write_csv(out/"stage8_events.csv",events,["frame_index","event","object_ids"]); write_csv(out/"stage8_sensitivity.csv",sensitivity)
    continuity=statistics.median(r["continuity"] for r in persistent) if persistent else 0.0; sensitivity_medians={}
    grouped=defaultdict(list)
    for r in sensitivity: grouped[(r["quantile"],r["gamma2_threshold"])].append(r["cores"])
    for k,v in grouped.items(): sensitivity_medians[f"q={k[0]:.3f},gamma2={k[1]:.2f}"]=statistics.median(v)
    baseline=statistics.median(r["cores"] for r in per_frame); spread=(max(sensitivity_medians.values())-min(sensitivity_medians.values()))/max(baseline,1.0) if sensitivity_medians else math.inf
    gates={"raw_sequence_complete":"pass" if len(steps)==len(required) else "fail","finite_fields":"pass","track_fragmentation":"pass" if len(summaries)<=cfg["maximum_reference_tracks"] else "fail","persistent_continuity":"pass" if continuity>=cfg["minimum_median_continuity"] else "fail","threshold_stability":"pass" if spread<=cfg["maximum_relative_sensitivity_spread"] else "fail"}
    passed=all(v=="pass" for v in gates.values())
    report={"schema_version":1,"status":"completed","created_at_utc":datetime.now(timezone.utc).isoformat(),"case_id":cfg["case_id"],"project_commit":git_head(root.parents[1]),"mfc_commit":git_head(mfc),"case_dir":str(case),"frames":len(steps),"catalogue_rows":len(all_rows),"tracks":len(summaries),"persistent_tracks":len(persistent),"median_persistent_continuity":continuity,"possible_topology_events":len(events),"relative_sensitivity_spread":spread,"sensitivity_median_cores":sensitivity_medians,"per_frame":per_frame,"gates":gates,"claim_gate":"physics_catalogue_ready_for_method_comparison" if passed else "physics_catalogue_requires_revision","limitations":["This is a two-dimensional core catalogue, not a three-dimensional vortex-tube segmentation.","Gamma2 stencil and criterion thresholds require cross-case validation.","Possible merge/split events are audit flags and not forced topology labels."]}
    (out/"stage8_report.json").write_text(json.dumps(report,indent=2)+"\n")
    print("STAGE8_STATUS=completed"); print(f"STAGE8_ROWS={len(all_rows)}"); print(f"STAGE8_TRACKS={len(summaries)}"); print(f"STAGE8_PERSISTENT_TRACKS={len(persistent)}"); print(f"STAGE8_MEDIAN_CONTINUITY={continuity:.6f}"); print(f"STAGE8_CLAIM_GATE={report['claim_gate']}"); print(f"STAGE8_REPORT={out/'stage8_report.json'}")
    return 0 if passed else 8

if __name__=="__main__": raise SystemExit(main())
