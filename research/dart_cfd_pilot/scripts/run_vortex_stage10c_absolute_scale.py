#!/usr/bin/env python3
"""Hardened train/test benchmark for an absolute, scale-adaptive detector."""
from __future__ import annotations

import argparse, csv, json, math
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter
from scipy.optimize import linear_sum_assignment


@dataclass(frozen=True)
class Vortex:
    x: float; y: float; circulation: float; radius: float


def add_lamb_oseen(xx, yy, vortices):
    u=np.zeros_like(xx); v=np.zeros_like(xx)
    for q in vortices:
        dx,dy=xx-q.x,yy-q.y; r2=dx*dx+dy*dy
        f=q.circulation/(2*math.pi)*(1-np.exp(-r2/q.radius**2))/np.maximum(r2,1e-14)
        u-=f*dy; v+=f*dx
    return u,v


def make_case(rng, family, x, y):
    xx,yy=np.meshgrid(x,y,indexing='ij'); truth=[]; u=np.zeros_like(xx); v=np.zeros_like(xx)
    if family=='wall_image':
        q=Vortex(rng.uniform(-.55,.55),rng.uniform(-.67,-.54),rng.choice([-1,1])*rng.uniform(.5,1.4),rng.uniform(.08,.18)); truth=[q]
        image=Vortex(q.x,-1.6-q.y,-q.circulation,q.radius); u,v=add_lamb_oseen(xx,yy,[q,image])
        # Thin no-slip-like background layer parallel to the bottom wall.
        u+=rng.uniform(-.8,.8)*np.exp(-(yy+.8)/rng.uniform(.025,.06))
    elif family=='stuart_shear':
        eps=rng.uniform(.25,.55); k=math.pi/rng.uniform(.75,1.05); y0=rng.uniform(-.15,.15); phase=rng.uniform(-.25,.25)
        den=np.cosh(k*(yy-y0))+eps*np.cos(k*(xx-phase))
        u=k*np.sinh(k*(yy-y0))/den
        v=k*eps*np.sin(k*(xx-phase))/den
        # Centers occur at cos(k(x-phase))=-1 within the domain.
        for n in range(-3,4):
            xc=phase+(2*n+1)*math.pi/k
            if -1.0<=xc<=1.0: truth.append(Vortex(xc,y0,-1.0,.18))
    else:
        if family in {'close_resolved','merger'}:
            a=rng.uniform(.08,.18); sep=rng.uniform(3.5,5.0)*a if family=='close_resolved' else rng.uniform(1.5,3.2)*a; ang=rng.uniform(0,2*math.pi); cx,cy=rng.uniform(-.35,.35),rng.uniform(-.25,.25)
            sign2=rng.choice([-1,1]) if family=='close_resolved' else 1
            truth=[Vortex(cx-sep*np.cos(ang)/2,cy-sep*np.sin(ang)/2,rng.uniform(.6,1.4),a),Vortex(cx+sep*np.cos(ang)/2,cy+sep*np.sin(ang)/2,sign2*rng.uniform(.45,1.3),rng.uniform(.07,.22))]
        else:
            n=int(rng.integers(1,5)); truth=[]
            for _ in range(n):
                for _try in range(100):
                    xp,yp=rng.uniform(-.76,.76),rng.uniform(-.55,.55)
                    if all(math.hypot(xp-q.x,yp-q.y)>.25 for q in truth): break
                lo=.18 if family=='scale' else .42; rad=(rng.uniform(.045,.30) if family=='scale' else rng.uniform(.08,.21))
                truth.append(Vortex(xp,yp,rng.choice([-1,1])*rng.uniform(lo,1.5),rad))
        u,v=add_lamb_oseen(xx,yy,truth)
    if family in {'random','close_resolved','merger','scale','correlated_noise'}:
        u+=rng.uniform(0,.55)*yy
    if family=='shock_clutter':
        # Irrotational oblique compression-like jump plus genuine vortices.
        nx,ny=math.cos(.65),math.sin(.65); s=(xx*nx+yy*ny-rng.uniform(-.2,.2))/rng.uniform(.018,.045); amp=rng.uniform(.5,1.3)
        u+=amp*nx*np.tanh(s); v+=amp*ny*np.tanh(s)
    noise=.015
    if family=='correlated_noise': noise=rng.uniform(.05,.11)
    rawu=rng.standard_normal(u.shape); rawv=rng.standard_normal(v.shape)
    corr=rng.uniform(1.2,4.0); rawu=gaussian_filter(rawu,corr); rawv=gaussian_filter(rawv,corr)
    rms=max(float(np.sqrt(np.mean(u*u+v*v))),.2); nr=max(float(np.std(rawu)),1e-12)
    u+=noise*rms*rawu/nr; v+=noise*rms*rawv/max(float(np.std(rawv)),1e-12)
    return u,v,truth


def gamma_at(x,y,u,v,omega,i,j,radius):
    i0=max(0,i-radius); i1=min(len(x),i+radius+1); j0=max(0,j-radius); j1=min(len(y),j+radius+1)
    uu=u[i0:i1,j0:j1]; vv=v[i0:i1,j0:j1]; rx=x[i0:i1,None]-x[i]; ry=y[None,j0:j1]-y[j]
    mask=rx*rx+ry*ry<=(radius*max(np.median(np.diff(x)),np.median(np.diff(y))))**2
    du=uu-np.mean(uu[mask]); dv=vv-np.mean(vv[mask]); den=np.sqrt(rx*rx+ry*ry)*np.sqrt(du*du+dv*dv)
    cross=rx*dv-ry*du; valid=mask&(den>1e-14)
    gamma=float(np.mean(cross[valid]/den[valid])) if np.any(valid) else 0.0
    ww=omega[i0:i1,j0:j1][mask]; coherence=float(abs(np.sum(ww))/max(np.sum(np.abs(ww)),1e-14))
    return gamma,coherence


def detect(x,y,u0,v0,cfg):
    maximum_detections=int(cfg.get('maximum_detections',40))
    if maximum_detections<1:raise ValueError('maximum_detections must be at least one')
    dx=float(np.median(np.diff(x))); candidates=[]
    for sigma in cfg['scales']:
        u=gaussian_filter(u0,sigma,mode='nearest'); v=gaussian_filter(v0,sigma,mode='nearest')
        dux,duy=np.gradient(u,x,y,edge_order=2); dvx,dvy=np.gradient(v,x,y,edge_order=2)
        omega=dvx-duy; tr=dux+dvy; det=dux*dvy-duy*dvx; disc=tr*tr-4*det; lci=.5*np.sqrt(np.maximum(-disc,0))
        strain2=dux*dux+dvy*dvy+.5*(duy+dvx)**2; rot2=.5*omega*omega; ratio=rot2/(rot2+strain2+1e-12)
        finite=lci[np.isfinite(lci)]; med=float(np.median(finite)); mad=float(np.median(np.abs(finite-med)))
        robust_floor=med+cfg['minimum_lci_snr']*1.4826*max(mad,1e-12)
        omed=float(np.median(omega)); omad=float(np.median(np.abs(omega-omed)))
        rotation_floor=cfg['rotation_floor_factor']*1.4826*max(omad,1e-12)
        threshold=max(cfg['minimum_lambda_ci'],robust_floor,rotation_floor)
        size=max(3,2*int(round(sigma))+1); peaks=(lci==maximum_filter(lci,size=size,mode='nearest'))&(lci>=threshold)&(ratio>=cfg['minimum_omega_ratio'])&(det>0)
        margin=max(2,int(round(1.5*sigma)))
        for i,j in np.argwhere(peaks):
            if i<margin or j<margin or i>=len(x)-margin or j>=len(y)-margin: continue
            if x[i]-x[0]<cfg['analysis_boundary_margin'] or x[-1]-x[i]<cfg['analysis_boundary_margin'] or y[j]-y[0]<cfg['analysis_boundary_margin'] or y[-1]-y[j]<cfg['analysis_boundary_margin']: continue
            radius=max(2,int(round(cfg['gamma_window_factor']*sigma)))
            g,coherence=gamma_at(x,y,u,v,omega,int(i),int(j),radius)
            if abs(g)<cfg['minimum_absolute_gamma2'] or coherence<cfg['minimum_sign_coherence']: continue
            scale_radius=max(1.5*sigma*dx,dx)
            score=float((sigma**2)*lci[i,j]*abs(g)*ratio[i,j]*coherence)
            candidates.append({'x':float(x[i]),'y':float(y[j]),'sign':1 if omega[i,j]>=0 else -1,'radius':scale_radius,'score':score,'sigma':sigma,'lambda_ci':float(lci[i,j]),'gamma2':g,'omega_ratio':float(ratio[i,j]),'sign_coherence':coherence})
    accepted=[]
    for q in sorted(candidates,key=lambda z:-z['score']):
        if any(q['sign']==a['sign'] and math.hypot(q['x']-a['x'],q['y']-a['y'])<max(cfg['minimum_nms_radius'],cfg['nms_radius_factor']*max(q['radius'],a['radius'])) for a in accepted): continue
        accepted.append(q)
        if len(accepted)>=maximum_detections: break
    return accepted


def match(det,truth):
    if not det:return 0,0,len(truth),[]
    c=np.full((len(det),len(truth)),1e6)
    for i,d in enumerate(det):
        for j,t in enumerate(truth):
            if d['sign']==(1 if t.circulation>0 else -1): c[i,j]=math.hypot(d['x']-t.x,d['y']-t.y)/max(t.radius,1e-9)
    ri,ci=linear_sum_assignment(c); dist=[float(c[i,j]) for i,j in zip(ri,ci) if c[i,j]<=.75]; tp=len(dist)
    return tp,len(det)-tp,len(truth)-tp,dist


def specs(count,seed,x,y):
    rng=np.random.default_rng(seed); families=['random','close_resolved','merger','wall_image','stuart_shear','correlated_noise','scale','shock_clutter']; out=[]
    for i in range(count):
        f=families[i%len(families)]; u,v,t=make_case(rng,f,x,y); out.append((f,u,v,t))
    return out


def evaluate(x,y,data,cfg):
    rows=[]
    for i,(family,u,v,truth) in enumerate(data):
        d=detect(x,y,u,v,cfg); tp,fp,fn,nd=match(d,truth); rows.append({'case':i,'family':family,'truth':len(truth),'detections':len(d),'tp':tp,'fp':fp,'fn':fn,'matched':len(nd),'normalized_squared_error_sum':sum(z*z for z in nd)})
    tp=sum(r['tp'] for r in rows);fp=sum(r['fp'] for r in rows);fn=sum(r['fn'] for r in rows);p=tp/max(tp+fp,1);rc=tp/max(tp+fn,1);f=2*p*rc/max(p+rc,1e-15);n=sum(r['matched'] for r in rows);rmse=math.sqrt(sum(r['normalized_squared_error_sum'] for r in rows)/n) if n else math.inf
    return {'precision':p,'recall':rc,'f1':f,'normalized_center_rmse':rmse,'tp':tp,'fp':fp,'fn':fn},rows


def aggregate(rows):
    tp=sum(r['tp'] for r in rows);fp=sum(r['fp'] for r in rows);fn=sum(r['fn'] for r in rows);p=tp/max(tp+fp,1);rc=tp/max(tp+fn,1);f=2*p*rc/max(p+rc,1e-15);n=sum(r['matched'] for r in rows);rmse=math.sqrt(sum(r['normalized_squared_error_sum'] for r in rows)/n) if n else math.inf
    return {'precision':p,'recall':rc,'f1':f,'normalized_center_rmse':rmse,'tp':tp,'fp':fp,'fn':fn}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output-dir',type=Path,required=True);ap.add_argument('--train-cases',type=int,default=70);ap.add_argument('--test-cases',type=int,default=210);a=ap.parse_args();a.output_dir.mkdir(parents=True,exist_ok=True)
    x=np.linspace(-1,1,161);y=np.linspace(-.8,.8,129);train=specs(a.train_cases,20260905,x,y);test=specs(a.test_cases,20260906,x,y);grid=[]
    for lci in [.50,1.0,2.0]:
      for gamma in [.70,.80]:
       for rotation_floor in [.75,1.5,2.5]:
        for coherence in [.65,.80]:
         cfg={'scales':[1.,2.,4.,8.,12.],'minimum_lambda_ci':lci,'minimum_absolute_gamma2':gamma,'minimum_omega_ratio':.70,'minimum_lci_snr':6.0,'rotation_floor_factor':rotation_floor,'minimum_sign_coherence':coherence,'gamma_window_factor':2.0,'nms_radius_factor':2.0,'minimum_nms_radius':.08,'analysis_boundary_margin':.10}
         m,_=evaluate(x,y,train,cfg);grid.append({**cfg,**{f'train_{k}':v for k,v in m.items()}})
    grid.sort(key=lambda r:(-r['train_f1'],-r['train_recall'],r['train_normalized_center_rmse']));best=grid[0];cfg={k:best[k] for k in ['scales','minimum_lambda_ci','minimum_absolute_gamma2','minimum_omega_ratio','minimum_lci_snr','rotation_floor_factor','minimum_sign_coherence','gamma_window_factor','nms_radius_factor','minimum_nms_radius','analysis_boundary_margin']}
    train_m,_=evaluate(x,y,train,cfg);test_m,rows=evaluate(x,y,test,cfg);family_names=['random','close_resolved','merger','wall_image','stuart_shear','correlated_noise','scale','shock_clutter'];families={f:evaluate(x,y,[z for z in test if z[0]==f],cfg)[0] for f in family_names}
    rng=np.random.default_rng(20260907);boots=[]
    for _ in range(2000): boots.append(aggregate([rows[i] for i in rng.integers(0,len(rows),len(rows))]))
    ci={k:[float(np.quantile([b[k] for b in boots],.025)),float(np.quantile([b[k] for b in boots],.975))] for k in ['precision','recall','f1','normalized_center_rmse']}
    core_families=[f for f in family_names if f!='merger']
    gates={'test_f1_lower_ci':'pass' if ci['f1'][0]>=.85 else 'fail','every_resolved_family_recall':'pass' if min(families[f]['recall'] for f in core_families)>=.70 else 'fail','localization_upper_ci':'pass' if ci['normalized_center_rmse'][1]<=.25 else 'fail'}
    report={'status':'completed','train_cases':len(train),'test_cases':len(test),'grid_points':len(grid),'selected':cfg,'train_metrics':train_m,'test_metrics':test_m,'confidence_intervals_95':ci,'family_metrics':families,'gates':gates,'claim_gate':'absolute_scale_detector_pass' if all(v=='pass' for v in gates.values()) else 'absolute_scale_detector_requires_revision','notes':['wall_image uses an opposite-sign image vortex and a near-wall shear layer','noise is spatially correlated','matching localization is normalized by the true core radius','merger cases are reported but excluded from the two-resolved-core recall gate and reserved for Stage 10D event evaluation']}
    (a.output_dir/'stage10c_report.json').write_text(json.dumps(report,indent=2)+'\n')
    for name,data in [('stage10c_grid.csv',grid),('stage10c_test_cases.csv',rows)]:
      with (a.output_dir/name).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    print(json.dumps(report,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
