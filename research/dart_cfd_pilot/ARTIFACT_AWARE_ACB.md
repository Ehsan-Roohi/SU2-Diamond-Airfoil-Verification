# Artifact-Aware ACB-CMCD

Artifact-Aware Adaptive Candidate-Budget Coherent-Motion Core Detection
(AA-ACB-CMCD) is the post-audit successor to ACB-CMCD. It retains the locked
Q-criterion candidate generator and adaptive budget while adding predeclared,
interpretable physical vetoes for three recurrent compressible-CFD failure
modes:

1. immersed-boundary and wall-attached vorticity sheets;
2. grid-locked alternating-sign beads on captured shocks; and
3. shear-layer ridges without closed-core kinematics.

The vetoes use wall distance, compressive dilatation relative to vorticity,
swirling-strength purity, signed-vorticity coherence, velocity circulation on
a local ring, multiscale peak persistence, and Q-Hessian compactness. No image
classifier or DART/SAM inference is used.

## Audit protocol

The configuration in `vortex_artifact_aware_acb.json` is fixed before the
runner loads the visual labels. Detections are generated for every frame first;
only then does the runner open
`reference/acb_cmcd_blind_visual_audit.csv` and compute a confusion matrix.
Nevertheless, the audit already exposed the wall, shock, and shear-layer
failure families and therefore informed this method's feature design. These
36 samples are consequently a development/resubstitution diagnostic, not a
new blind-validation set or a prevalence-weighted precision estimate. The
next case must keep all parameters frozen and use independent human labels.

## Unity execution

The run reuses the completed Mach-3, alpha-30 raw MFC fields and does not run
MFC again. The Slurm wrapper runs the regression tests, processes 61 snapshots,
writes three physical comparison figures, and creates a flat archive in the
repository root:

```text
VORTEX_ARTIFACT_AWARE_ACB_JOBID_COMPLETE.tar.gz
```

Scientific failure does not masquerade as a scheduler failure: a technically
complete job exits zero and records its scientific decision in
`artifact_aware_acb_report.json`.
