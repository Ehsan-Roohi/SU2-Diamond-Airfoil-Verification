# Tim Colonius high-viscosity diagnostic

This is a deliberately separate two-grid screening experiment motivated by
Tim Colonius's recommendation to raise viscosity until grid convergence can
be demonstrated, then lower viscosity in stages.  Tim did not prescribe a
specific Reynolds number; this first pilot uses `Re_c=10^4`, one hundred
times the viscosity of the `Re_c=10^6` article baseline.

Fresh HLL/WENO5 runs on `f180` and `f270` advance from `t=0` to `t=6`, save
every `0.05`, and retain the validated 32-rank layout.  Both jobs run
independently and email on begin, end, or failure.  This pilot is a screening
step, not by itself a final grid-convergence claim.  The next decision is to
compare force/shock metrics across the two grids and either add `f405` or
reduce viscosity (for example to `Re_c=3e4`).
