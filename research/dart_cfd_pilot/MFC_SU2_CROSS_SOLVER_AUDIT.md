# Frozen MFC/SU2 cross-solver audit

This audit applies the published TSA-SRA-CMCD-v2 detector without threshold
changes to the existing Mach-3, alpha-40 MFC ILES sequence and SU2 SST-URANS
checkpoint. Both airfoil cases were inspected during earlier detector
development, so the result is retrospective diagnostic evidence and is never
reported as an independent holdout.

## Reference and scoring policy

- MFC uses the pre-existing 61-frame Stage-8 physics catalogue. That catalogue
  was frozen before this cross-solver runner was written.
- SU2 is the predeclared shock-rich negative control. The raw Gamma2 component
  census is retained to expose its known shock/grid artifacts, but those
  components are not treated as vortex truth.
- MFC centres are matched one-to-one within `0.08 c`; precision, recall, F1,
  rotation sign, localization error and near-body false positives are reported.
- SU2 passes its negative-control gate only when the detector returns no false
  vortex cores. Its two adjacent restart states cannot exercise the temporal
  part of TSA-SRA-CMCD-v2, and the temporal gate therefore fails closed.
- The cylinder-derived body exclusion is transferred geometrically: its
  original `0.75 D` centre-radius rule corresponds to a `0.25 D` wall
  clearance. The airfoil runner computes that clearance from the exact diamond
  mask instead of pretending the body is circular.

## Local SU2 result

The raw checkpoint contains 740 Q candidates per snapshot and 306 raw Gamma2
components across the two states. All selected candidates fail the frozen
closed-island, multi-radius winding, or pressure-corroboration gates. The final
detector count is zero, which passes the predeclared negative-control false-core
gate. Precision and recall are not defined for a zero-vortex negative control.

This is useful evidence that the frozen method does not mistake the prominent
shock ridges and O-grid interpolation structure for vortices. It is not a
positive-vortex or temporal SU2 validation.

## Unity reproduction

The batch job evaluates MFC and SU2 in one run and writes one flat archive at
the repository root:

```bash
sbatch --export=ALL \
  research/dart_cfd_pilot/scripts/submit_unity_vortex_mfc_su2_cross_solver.sh
```

```text
VORTEX_MFC_SU2_CROSS_SOLVER_JOBID_COMPLETE.tar.gz
VORTEX_MFC_SU2_CROSS_SOLVER_JOBID_COMPLETE.tar.gz.sha256.txt
```

The archive contains physical vorticity figures, reference and detector CSVs,
per-frame metrics, temporal recovery audit for MFC, reports for both solvers,
and environment provenance.

## Publication boundary

A positive MFC result plus a clean SU2 negative-control result would support
solver-transfer robustness. It still cannot establish an independent JCP-level
claim because both alpha-40 cases were visible during development. A future
paper must add a frozen, time-resolved SU2 or MFC airfoil sequence that was not
used to define any detector rule.
