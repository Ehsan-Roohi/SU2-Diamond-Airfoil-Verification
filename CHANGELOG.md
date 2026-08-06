# Change log

## 1.1.0-su2-only-report-reproduction — 2026-08-06

- Added audited Euler cases at 1°, 2°, and 3° so the report load sweep uses
  native SU2 output from 0° through 4°.
- Replaced the former custom-code report evidence with SU2 field panels,
  numerical Schlieren, native-grid wave measurements, and archived force
  windows.
- Added the report-asset generator, SU2-only field surrogate, common-scale
  comparison templates, reference figures, and exact reproduction targets.
- Added downloadable student-package and revised-Word assets to the tagged
  GitHub release.
- Retained the residual plateau as an explicit `QUALIFIED_PASS` warning; no
  grid-independence or hard-convergence claim is made.

## 1.0.0-public-book-companion — 2026-08-02

- Published the teaching package as a structured public GitHub repository.
- Added citation metadata, an MIT license, integrity guidance, and continuous
  integration for the 19 tests that do not require SU2.
- Added the sharp diamond-airfoil vertex file and explicit geometry notes.
- Added an optional Codex workflow that records compute resources and applies
  the same fail-closed numerical checks as the local runner.
- Clarified the book-level scientific status: Euler at zero incidence is a
  qualified teaching reference, not a grid benchmark; the other eight
  physical configurations remain unverified teaching cases.

## 3.0-fail-closed-audit — 2026-08-02

- Recognize the real SU2 warning spellings `nonphysical`, `non-physical`, and
  `non physical`; retain every matched line in the run manifest.
- Separate installation-smoke policy from production acceptance. Smoke runs
  record nonphysical warnings but make no convergence or physics claim.
- Require the complete configured force window (200 samples by default) for a
  production result.
- Record initial/final density residuals and their signed reduction in orders;
  enforce `residual_drop_min_orders` whenever an archived value is populated.
- Use an absolute CL peak-to-peak criterion at zero incidence. The qualified
  sharp-wall Euler case uses its controlled-run limit; unverified viscous rows
  remain `TBD` rather than inheriting it.
- Enforce populated CL/CD ranges and populated symmetry, shock-angle-error, and
  maximum-y+ limits.
- Add `extract_wave_metrics.py` for native-grid density-gradient ridge fitting,
  mirrored-density symmetry, and optional wall-y+ summary. The ridge selection
  is exported to CSV for visual audit.
- Add synthetic extraction tests, a real-wording SU2 log regression, smoke and
  production-policy tests, and static audits of all 18 configurations.
- Preserve `TEACHING_CONFIG_NOT_YET_REFERENCE_VERIFIED` for eight cases.
- Add a piecewise-sharp Euler mesh matching the analytical wall and replace the
  catastrophic Roe path with controlled HLLC settings (600 startup iterations
  at CFL 0.1; 2000 MUSCL iterations at CFL 0.2). The audited alpha-zero row is a
  qualified teaching reference with strict force, physicality, symmetry, and
  shock-angle checks; its residual plateau remains a visible warning and no
  grid-independence claim is made. Include the exact mesh generator as an
  optional advanced artifact.

## 2.0-teaching-audit — 2026-08-01

- Added the two-stage fail-closed wrapper, smoke test, OpenMP guidance, explicit
  SST inputs, current restart/history names, and package integrity metadata.
