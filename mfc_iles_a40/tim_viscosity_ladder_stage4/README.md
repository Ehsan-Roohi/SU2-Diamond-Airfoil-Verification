# Stage 4: intermediate-viscosity screening

This workflow implements the next adaptive step in the Tim Colonius
viscosity ladder for the two-dimensional Mach-3, alpha=40-degree diamond
airfoil.

It submits two fresh `f180` MFC runs:

- `Re_c = 5e4`, `t = 0..6`
- `Re_c = 1e5`, `t = 0..6`

Both retain the validated 32-rank HLL/unmapped-WENO5/immersed-boundary
configuration.  They save every `0.1` time unit (61 synchronized fluid and
IB snapshots per case), reducing total raw-field storage to about 17.4 GB.

The runs are screening controls, not final convergence claims.  After they
finish, compare bow-shock stand-off/angle, recirculation size, force statistics,
and resolved shear-layer structure against the completed `Re_c=1e4` and
`Re_c=1e6` cases.  Only the Reynolds number where fine-scale structure first
reappears should then be repeated on `f270`.

The submitter is fail-closed: it verifies the source case and STL hashes, the
MFC configuration, free space, the pinned workflow revision, and duplicate
active jobs before submitting.
