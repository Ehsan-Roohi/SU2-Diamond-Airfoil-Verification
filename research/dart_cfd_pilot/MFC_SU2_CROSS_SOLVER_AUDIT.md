# Frozen MFC/SU2 cross-solver audit

This audit applies the published TSA-SRA-CMCD-v2 detector without threshold
changes to the existing Mach-3, alpha-40 MFC ILES sequence and SU2 SST-URANS
checkpoint. Both airfoil cases were inspected during earlier detector
development, so the result is retrospective diagnostic evidence and is never
reported as an independent holdout.

## Reference and scoring policy

- MFC uses the pre-existing 61-frame Stage-8 physics catalogue. That catalogue
  was frozen before this cross-solver runner was written.
- SU2 is unlabelled. It must not be treated as a zero-vortex negative control.
  The raw Gamma2 component census is retained to expose shock/grid artifacts,
  but those components are neither accepted detections nor vortex truth.
- MFC centres are matched one-to-one within `0.08 c`; precision, recall, F1,
  rotation sign, localization error and near-body false positives are reported.
- The SU2 audit reports strong closed-Q/multiring-winding candidates rejected
  by the frozen detector as possible false negatives. Its two adjacent restart
  states cannot exercise the temporal part of TSA-SRA-CMCD-v2, and the temporal
  gate therefore fails closed.
- The airfoil runner computes wall distance from the exact diamond mask instead
  of pretending the body is circular. The former cylinder-equivalent `0.25 c`
  diagnostic is retained only for provenance and is not used to declare an
  airfoil vortex false.

## Local SU2 result

The corrected native-connectivity audit removes nonphysical triangles spanning
the O-grid hole and omits unqualified Gamma2 `+` markers from the accepted-core
figure. The frozen detector still returns zero cores, but each snapshot contains
one rejected candidate near `(x/c,y/c)=(0.9574,0.0944)` with a closed Q island,
three-of-three winding support, rotation purity about `0.71`, unit sign/ring
coherence, and unit scale persistence. It was rejected because the pressure
minimum is displaced while the core overlaps the detected shock ridge.

Therefore the former `zero false vortex` interpretation is withdrawn. The
correct claim gate is
`frozen_detector_missed_strong_su2_topology_requires_method_revision`. The
yellow ring in the physical figure marks a likely false negative, not an
accepted detection or ground truth label.

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

A positive MFC result and a corrected shock-embedded rescue that survives the
existing analytic controls and a new unseen time-resolved airfoil case could
support solver-transfer robustness. The present SU2 snapshots are a failure
analysis, not a validation result, because they were inspected while defining
the next rule and contain only two adjacent times.
