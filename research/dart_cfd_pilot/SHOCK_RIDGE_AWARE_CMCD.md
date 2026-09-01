# Shock-Ridge-Aware CMCD

Shock-Ridge-Aware Coherent-Motion Core Detection (SRA-CMCD) is a conservative
revision of the frozen AA-ACB-CMCD detector for compressible-flow fields.  It
was motivated by a solver-transfer failure on the SU2 Mach-3, alpha-40
SST-URANS checkpoint: the frozen MFC detector selected 43 locations, most on
shock, wall, or shear structures.

The revision does not change the frozen Q candidate generator or adaptive
candidate budget.  It adds four fail-closed requirements:

1. the Q component around a candidate is closed and not strongly elongated;
2. residual velocity has unit absolute phase winding and sign-consistent
   tangential motion on at least two of three physical rings;
3. a nearby pressure minimum is corroborated on at least two rings; and
4. the candidate is farther than eight raster cells from a simultaneous
   dimensionless pressure-gradient and entropy-gradient ridge.

The SU2 adapter differentiates velocity on the native 181 x 720 periodic
O-grid before interpolating derived quantities to the common Cartesian audit
raster.  This avoids Delaunay-triangle derivative artifacts.

## Scientific role

The alpha-40 SU2 checkpoint is an unlabelled development diagnostic. It informed
the thermodynamic ridge veto and is permanently excluded from independent
validation.  Its two restart states are adjacent and nearly identical, so
exact candidate persistence between them is explicitly reported as temporal
aliasing rather than tracking evidence.  The checkpoint itself records
`CHECKPOINTED / NOT_QUALIFIED` and cannot support a standalone CFD-validation
claim.

SRA-CMCD becomes a publication candidate only after its now-frozen thresholds
are evaluated without recalibration on independently annotated, time-resolved
cross-case data.  Required future cases include an analytic/adversarial suite,
a cylinder wake, and at least two additional airfoil conditions.  DART remains
a baseline rather than the proposed detector.

The local reproducibility audit of restart states 11999 and 12000 produced the
same fail-closed sequence in each state: 128 robust Q candidates, 97 surviving
the original artifact veto, 43 selected by frozen AA-ACB-CMCD, one surviving
closed-loop and pressure corroboration, and zero after the thermodynamic
shock-ridge veto.  The rejected last candidate was 7.07 raster cells from the
pressure/entropy ridge.  This is a successful negative-control outcome, not a
vortex-detection validation result.

The analytic positive-control audit subsequently exposed a directional
invariance bug in the original winding implementation: multiplying vector
phase winding by vorticity sign rejected all clockwise vortices.  SRA-CMCD now
uses absolute phase winding for topology and signed tangential velocity for
rotation direction.  This correction changes no numerical threshold and must
be rechecked on both the unlabelled SU2 diagnostic and all positive controls.

The analytic audit also exposed an artificial fixed-window failure for a
co-rotating pair separated by `0.16c`. The positive-`Q` island test now begins
with the original 16-cell window and expands, without changing its threshold,
up to 48 cells only when the connected component reaches an artificial window
edge. A component reaching the physical domain boundary or the maximum window
remains open, so shock-aligned ridges still fail closed.

Pressure corroboration also requires the detected rotation center and the
local pressure minimum to lie within two raster cells. A displacement up to
three cells is allowed only when an opposite-sign partner with comparable
strength independently passes the island, winding, pressure-ring, and
shock-distance checks. Within a ten-cell neighborhood, a same-sign peak below
65% of a stronger peak is rejected as a subordinate when its pressure minimum
is displaced by more than two cells. This rejects noise satellites without
enlarging NMS, while equal-strength co-rotating pairs and corroborated dipoles
remain separate.

## Unity execution

The submission script reads the existing final SU2 checkpoint and does not
rerun SU2.  It also disables Python's user-site packages and bootstraps a
pinned CPU analysis stack only when the isolated import check fails.  This
prevents an incomplete `~/.local` Matplotlib installation from shadowing the
project environment:

```bash
sbatch --export=ALL \
  research/dart_cfd_pilot/scripts/submit_unity_vortex_shock_ridge_aware.sh
```

It writes the downloadable archive directly in the repository root:

```text
VORTEX_SHOCK_RIDGE_CMCD_JOBID_COMPLETE.tar.gz
VORTEX_SHOCK_RIDGE_CMCD_JOBID_COMPLETE.tar.gz.sha256.txt
```

The archive contains flat scientific outputs: per-candidate audit CSV,
per-snapshot counts, final detections, JSON report, full-field physical
figures, a shock-bead zoom, and environment provenance.
