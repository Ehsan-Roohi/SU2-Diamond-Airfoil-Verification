# SU2 v8.5.0 `euler_alpha0` controlled tuning report

> **Package decision.** Revision 3 distributes the sharp 720 x 181 Euler mesh
> with HLLC, CFL 0.1 for 600 startup iterations, and CFL 0.2 for a 2000-iteration
> second-order stage (equivalent to the audited 1500 + 500 continuation). The
> alpha-zero force, physicality, symmetry, and shock-angle ranges are enforced
> as a qualified teaching reference. The residual plateau remains a visible
> warning, and no grid-independence claim is made. Alpha 4 and 8 remain
> unverified teaching configurations.

Date: 2026-08-02 UTC.  All runs used the official SU2 v8.5.0 OMP binary at
`/tmp/su2-v8.5.0/runtime/bin/SU2_CFD`.  The source teaching package was not
modified; all calculations were made in `su2_tuning/`.

## Reproduced baseline failure

The independently archived package run used Roe, CFL 0.2 for 1,200 first-order
iterations and CFL 0.5 for 4,000 MUSCL/Venkatakrishnan iterations.  Its final
second-order state contained density from `5.42e-13` to `96.33`, with the
pathological points concentrated at the rounded trailing edge.  SU2 reported
47 non-physical solution points and 125 non-physical reconstructed states.
The last-200 mean drag was 0.0310189 and the final density residual was
-3.74147.

## Mesh diagnosis

The supplied 720 x 181 mesh is a viscous wall-normal mesh reused for Euler.  Its
first-cell height is approximately `2.67e-6 c`, and SU2 reports maximum dual-CV
face-area aspect ratio 2249.57.  More importantly, the wall generator rounds
all four vertices at `r/c=0.001`, including the trailing edge.  A supersonic
Euler slip solution follows that convex trailing-edge arc and produces a local
near-vacuum; the sharp shock-expansion theory does not contain that geometry.

For the converged-looking HLLC/Venkat field on the supplied mesh, direct wall
integration decomposed the drag as follows:

| wall primitive | drag contribution |
|---|---:|
| leading-edge arc | 0.00246084 |
| two front panels | 0.01657213 |
| two mid-chord arcs | 0.00000254 |
| two rear panels | 0.01029729 |
| trailing-edge arc | 0.00030123 |
| total | 0.02963403 |

Thus the rounded leading and trailing arcs contribute about 0.00276 drag by
themselves.  Comparing this total one-to-one with the sharp-geometry analytical
value 0.028501 is not a clean discretization-error test.

## Controlled runs (4 OMP threads)

The basic command in each case directory was:

```bash
/tmp/su2-v8.5.0/runtime/bin/SU2_CFD -t 4 startup.cfg
/tmp/su2-v8.5.0/runtime/bin/SU2_CFD -t 4 second_order.cfg
```

| Case | Main changes | Runtime | Final/last-200 result | SU2 physicality warning | Verdict |
|---|---|---:|---|---|---|
| HLLC first order, supplied mesh | HLLC, CFL 0.1, 800 it. | 1m23s | CD mean 0.0304534; CL about 3e-14; rmsRho -4.493 | none | robust startup |
| HLLC MUSCL/Venkat, supplied mesh | HLLC, CFL 0.2, 2500 it. | 3m48s | CD mean 0.0296431, p-p 0.0001092 (0.37%); CL p-p 3.49e-9; rmsRho -3.760 | 6 non-physical reconstructed states; 2 near-vacuum nodes in restart | stable loads, not publishable PASS |
| HLLC MUSCL/Barth-Jespersen, supplied mesh | limiter changed only, 1800 it. | 3m00s | CD mean 0.0296662; CL order 1e-7; rmsRho -4.187 | 2 non-physical points and 2 reconstructed states | more robust, still not PASS |
| limiter frozen after 500 | HLLC/Venkat, `LIMITER_ITER=500` | stopped at 929 it. | rmsRho rose to -1.80 and CD to 0.03205 | unstable trend | reject |
| HLLC first order, Euler-spaced test mesh | same wall, first cell `3e-4 c`, CFL 0.1, 600 it. | 1m02s | CD mean 0.0291245, p-p 2.65e-5 (0.091%); CL about 6e-13; rmsRho -4.476 | none | best robust teaching baseline |
| HLLC MUSCL/Venkat, Euler-spaced test mesh | CFL 0.2, 1800 it. | 3m13s | CD mean 0.0294939, p-p 1.90e-5 (0.065%); CL p-p 4.03e-11; rmsRho -3.841 | 2 non-physical points | stable loads, not strict PASS |
| HLLC first order, sharp radial O-grid | sharp four-corner diamond, first cell `3e-4 c`, CFL 0.1, 600 it. | 0m50s | CD mean 0.0272490; last-200 p-p 0.077%; CL about 8e-14; rmsRho -4.499 | none | robust startup |
| HLLC MUSCL/Venkat, sharp radial O-grid | CFL 0.2, 1500 it. plus 500-it continuation | 2m26s + 0m47s | continuation last-200 CD mean 0.0273643, p-p 0.0546%; CL about -4e-12; rmsRho -4.155 | none; no near-vacuum | physically clean teaching result |

The Euler-spaced test mesh reduced the maximum dual-CV face-area aspect ratio
from 2249.57 to 309.91.  It preserved the same rounded wall and far-field
location; it is a diagnostic mesh, not a replacement benchmark.

## Recommendation

No defensible, fully accepted `EXPECTED_RESULTS` row can be assigned to the
current rounded-wall second-order `euler_alpha0` case.  A configuration-only
change from Roe to HLLC removes the catastrophic failure, but cannot remove the
rounded-TE near-vacuum mechanism or create residual convergence.  The sharp
prototype removes every non-physical-state warning and gives exceptionally
stable symmetric loads.  Its density residual nevertheless plateaus near
`10^-4.2`, and its drag is about 4.0% below the sharp analytical value; it is a
clean teaching result, not yet a grid-qualified benchmark.

Revision 3 implements the defensible part of that recommendation: Euler cases
now use a separate, moderately spaced, piecewise-sharp O-grid with HLLC, low
CFL numbers, and a continuously active limiter.  The alpha-zero result is
therefore admitted only as a **qualified teaching reference**: its force,
physicality, symmetry, and wave-angle gates are strict, while its residual
plateau is printed as a warning.  It is not promoted to a benchmark.

The remaining research step is a clean three-grid sharp-wall family (ideally a
C-grid or another wake-cut topology) with identical numerics.  Until that study
exists, no Euler GCI or grid-independence claim is permitted.  Alpha 4 and 8
also remain fail-closed teaching configurations until their own audited ranges
are archived.  The residual target and physicality criteria must not be relaxed
merely to force a PASS.
