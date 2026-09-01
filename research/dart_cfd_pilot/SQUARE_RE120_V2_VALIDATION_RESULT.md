# Prospective square-cylinder Re=120 validation result

## Frozen protocol

- Detector: TSA-SRA-CMCD-v2.
- Local pre-execution detector and holdout freeze commit:
  `0b895f34e05e0c7f990a8ed1a551c4755713dc1c`.
- Published GitHub protocol-record commit (without this result document):
  `b6a782f772cb1da64096f2f339532d2ed296ad6c`.
- Holdout: two-dimensional D2Q9 BGK square-cylinder wake, Re=120, 5% blockage.
- Reference: independent Gamma2 components; Gamma2 was never used by the detector.
- The result was evaluated once after the detector, solver, matching rule, and
  all gates were committed.

## Result

The prospective holdout passed every predeclared gate.

| Metric | Observed | Gate | Result |
| --- | ---: | ---: | --- |
| Evaluated frames | 41 | >=30 | PASS |
| Reference vortices | 271 | >=40 | PASS |
| Strouhal number | 0.159667969 | 0.145-0.175 | PASS |
| Maximum density deviation | 0.039448867 | <=0.05 | PASS |
| Precision | 0.971544715 | >=0.80 | PASS |
| Recall | 0.881918819 | >=0.80 | PASS |
| F1 | 0.924564797 | reported | — |
| Rotation-sign accuracy | 1.000000000 | >=0.95 | PASS |
| Near-wall false positives | 0 | <=0 | PASS |

Confusion totals were 239 true positives, 7 false positives, and 32 false
negatives.  The machine-readable claim is
`prospective_temporal_cylinder_wake_validation_pass`.

## Interpretation boundary

This is positive prospective evidence that the detector's temporal revision
generalizes from Re=100 development data to an unseen Re=120 sequence without
changing any detector threshold.  It is not yet cross-solver evidence because
both sequences use the same canonical D2Q9 BGK generator, and it is not a
second untouched geometry because the square-cylinder family was already
observed.  The next publication-grade step is a frozen cross-solver holdout
using time-resolved SU2, MFC, or experimental velocity fields.
