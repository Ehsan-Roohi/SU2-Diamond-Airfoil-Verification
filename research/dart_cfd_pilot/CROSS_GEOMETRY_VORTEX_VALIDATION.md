# Frozen cross-geometry vortex validation

This protocol tests the frozen TSA-SRA-CMCD detector on a sharp-edged square
cylinder after development on circular-cylinder wakes.  It is deliberately
fail-closed: the detector, independent Gamma2 reference, matching rule, and
all acceptance thresholds are frozen before the first square-cylinder run.

## Untouched holdout

- Case: two-dimensional D2Q9 BGK square-cylinder wake, Re=150.
- Geometry: side length D, halfway bounce-back, 10% blockage.
- Detector freeze: `7d9b27753dde34787c0689168dc5c58fa7a1b1ad`.
- Holdout protocol freeze: `acd06a200d7854ed2938fbdbfc529e636a0166bf`.
- Independent reference: Gamma2 components; Gamma2 is not used by the detector.
- Predeclared frequency gate: 0.10 <= St <= 0.16.
- Detection gates: precision and recall >= 0.80, rotation-sign accuracy >= 0.95,
  and no near-wall false positives.

The first solver execution completed, but its monitor file ended in a partial
row before scoring.  No holdout metric was observed.  Commit `68cf561` made
monitor writes unbuffered and fail-closed; the exact frozen simulation and
evaluation protocol was then rerun in a new directory.

The scored holdout yielded St=0.169921875, precision=0.986899563,
recall=0.827838828, F1=0.900398406, rotation-sign accuracy=1.0, and zero
near-wall false positives.  Thus every detector-specific gate passed, but the
overall physics-validation claim failed because the predeclared frequency
gate failed.

## Post-holdout blockage diagnostic

A separately declared diagnostic doubled the transverse domain, reducing
blockage from 10% to 5%.  It is not an independent holdout and cannot rescue
the failed result.  St decreased only to 0.165527344; density stability and
recall also failed.  The discrepancy therefore cannot be attributed solely to
10% blockage, and this minimal BGK solver is not used as publication-grade
square-cylinder frequency evidence.

## Interpretation

The square-cylinder result is positive evidence for cross-geometry vortex-core
localization, because the frozen detector passes all core-detection gates
against an independent kinematic reference.  It is not a full physical
validation of the square-cylinder solver.  Publication claims must report the
failed frequency gate and use an independently verified CFD or experimental
sequence for the next cross-geometry test.
