#!/usr/bin/env python3
"""Stream MFC restart fields into a full-domain schlieren/vorticity movie for t=6..21."""
from __future__ import annotations
import argparse, csv, hashlib, re
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.animation as animation
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon

NX, NY, NVAR = 2970, 2700, 5
EXPECTED_BYTES = NVAR * NX * NY * 8
DT, FIRST, LAST, SAVE = 1/5400, 32400, 113400, 270
STAGES = ("t06_t11", "t11_t16", "t16_t21")
AIRFOIL = np.array([[0,0],[.5,.075],[1,0],[.5,-.075]], float)

# Match the already validated t=0..6 movies. Keeping both the camera and
# color normalization fixed prevents an artificial jump at the t=6 join.
VIEW_X = (-1.25, 4.75)
VIEW_Y = (-1.25, 4.75)
GRAD_MAX = 65.0
VORT_MAX = 17.0

def args():
    p=argparse.ArgumentParser()
    p.add_argument("chain",type=Path)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--fps",type=int,default=20)
    p.add_argument("--stride",type=int,default=1)
    p.add_argument("--x-min",type=float,default=VIEW_X[0])
    p.add_argument("--x-max",type=float,default=VIEW_X[1])
    p.add_argument("--y-min",type=float,default=VIEW_Y[0])
    p.add_argument("--y-max",type=float,default=VIEW_Y[1])
    p.add_argument("--grad-max",type=float,default=GRAD_MAX)
    p.add_argument("--vort-max",type=float,default=VORT_MAX)
    return p.parse_args()

def step(path):
    m=re.fullmatch(r"lustre_(\d+)\.dat",path.name)
    if not m: raise ValueError(path)
    return int(m.group(1))

def discover(chain,stride):
    found={}
    for stage in STAGES:
        for path in (chain/stage/"restart_data").glob("lustre_[0-9]*.dat"):
            s=step(path)
            if FIRST<=s<=LAST:
                if path.stat().st_size!=EXPECTED_BYTES:
                    raise RuntimeError(f"wrong size: {path}")
                found.setdefault(s,path)
    expected=list(range(FIRST,LAST+1,SAVE))
    missing=[s for s in expected if s not in found]
    if missing:
        raise RuntimeError(f"missing {len(missing)} checkpoints: {missing[:8]}")
    return [(s,found[s]) for s in expected][::stride]

def crop(xmin,xmax,ymin,ymax):
    x=np.linspace(-5,6,NX,endpoint=False)+.5*11/NX
    y=np.linspace(-5,5,NY,endpoint=False)+.5*10/NY
    ix=np.flatnonzero((x>=xmin)&(x<=xmax))
    iy=np.flatnonzero((y>=ymin)&(y<=ymax))
    if len(ix)<2 or len(iy)<2:
        raise RuntimeError("requested movie view does not intersect the MFC grid")
    xs=slice(ix[0],ix[-1]+1)
    ys=slice(iy[0],iy[-1]+1)
    return xs,ys,x[xs],y[ys],11/NX,10/NY

def fields(path,xs,ys,dx,dy):
    q=np.memmap(path,dtype="<f8",mode="r",shape=(NVAR,NY,NX))
    rho=np.asarray(q[0,ys,xs],np.float32)
    u=np.asarray(q[1,ys,xs],np.float32)/np.maximum(rho,1e-12)
    v=np.asarray(q[2,ys,xs],np.float32)/np.maximum(rho,1e-12)
    drdy,drdx=np.gradient(rho,dy,dx)
    dudy,_=np.gradient(u,dy,dx)
    _,dvdx=np.gradient(v,dy,dx)
    return np.hypot(drdx,drdy),dvdx-dudy

def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(8*1024*1024),b""):
            h.update(b)
    return h.hexdigest()

def main():
    a=args()
    if not (a.x_min<a.x_max and a.y_min<a.y_max):
        raise RuntimeError("invalid movie view bounds")
    if a.grad_max<=0 or a.vort_max<=0:
        raise RuntimeError("color limits must be positive")

    chain=a.chain.resolve()
    out=a.output.resolve()
    out.mkdir(parents=True,exist_ok=True)
    rec=discover(chain,a.stride)
    xs,ys,x,y,dx,dy=crop(a.x_min,a.x_max,a.y_min,a.y_max)

    movie=out/"MFC_HLL_T06_T21_SCHLIEREN_VORTICITY.mp4"
    preview=out/"MFC_HLL_T21_MOVIE_PREVIEW.png"
    manifest=out/"MFC_HLL_T06_T21_MOVIE_FRAMES.csv"

    fig,ax=plt.subplots(1,2,figsize=(15.5,7.6),constrained_layout=True)
    extent=(x[0],x[-1],y[0],y[-1])
    empty=np.zeros((len(y),len(x)),np.float32)
    im0=ax[0].imshow(empty,origin="lower",extent=extent,cmap="gray",vmin=0,vmax=a.grad_max)
    im1=ax[1].imshow(empty,origin="lower",extent=extent,cmap="RdBu_r",vmin=-a.vort_max,vmax=a.vort_max)
    titles=(r"Density-gradient magnitude $|\nabla\rho|$",r"Spanwise vorticity $\omega_z$")

    for z,title in zip(ax,titles):
        z.add_patch(Polygon(AIRFOIL,closed=True,facecolor="black",edgecolor="white",lw=1.2,zorder=5))
        z.set(xlim=(a.x_min,a.x_max),ylim=(a.y_min,a.y_max),
              xlabel=r"$x/c$",ylabel=r"$y/c$",title=title)
        z.set_aspect("equal")

    fig.colorbar(im0,ax=ax[0],fraction=.045,pad=.02)
    fig.colorbar(im1,ax=ax[1],fraction=.045,pad=.02)
    label=fig.suptitle("",fontsize=17,fontweight="bold")
    writer=animation.FFMpegWriter(
        fps=a.fps,codec="libx264",bitrate=6500,
        extra_args=["-pix_fmt","yuv420p","-movflags","+faststart"])

    with manifest.open("w",newline="") as f:
        w=csv.writer(f)
        w.writerow(("frame","step","time","source"))
        with writer.saving(fig,str(movie),dpi=140):
            for i,(s,p) in enumerate(rec):
                g,o=fields(p,xs,ys,dx,dy)
                im0.set_data(np.clip(g,0,a.grad_max))
                im1.set_data(np.clip(o,-a.vort_max,a.vort_max))
                label.set_text(f"MFC HLL, Mach 3, α = 40°    t = {s*DT:.2f}")
                writer.grab_frame(facecolor="white")
                w.writerow((i,s,f"{s*DT:.9f}",p))
                print(f"FRAME {i+1}/{len(rec)} step={s} t={s*DT:.2f}",flush=True)
            fig.savefig(preview,dpi=180,facecolor="white")

    plt.close(fig)
    if movie.stat().st_size<1_000_000:
        raise RuntimeError("movie unexpectedly small")

    (out/(movie.name+".sha256.txt")).write_text(f"{digest(movie)}  {movie.name}\n")
    (out/"MFC_HLL_T06_T21_MOVIE_OK.txt").write_text(
        "status=PASS\n"
        f"frames={len(rec)}\n"
        f"first_step={rec[0][0]}\n"
        f"last_step={rec[-1][0]}\n"
        f"save_dt={SAVE*DT:.9f}\n"
        f"fps={a.fps}\n"
        f"x_range={a.x_min}:{a.x_max}\n"
        f"y_range={a.y_min}:{a.y_max}\n"
        f"gradient_range=0:{a.grad_max}\n"
        f"vorticity_range={-a.vort_max}:{a.vort_max}\n")
    print(f"MOVIE_PASS={movie}")

if __name__=="__main__":
    raise SystemExit(main())
