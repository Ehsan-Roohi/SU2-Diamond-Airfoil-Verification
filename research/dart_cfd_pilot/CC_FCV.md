# Cross-Case Frozen-Configuration Validation (CC-FCV)

PG-RRD passed its analytic two-core and invariance tests but did not improve
the fixed alpha-40 temporal holdout. Its coverage was 0.8236 versus 0.8446 for
the calibrated Q detector, and its close-member coverage was 0.8658 versus
0.8805. PG-RRD is therefore retained as a documented negative result rather
than promoted as the primary detector.

CC-FCV tests the stronger and simpler Calibrated Multi-Criterion Core Detector
(CMCD) path without any additional tuning. The Q configuration selected from
the alpha-40 calibration interval is frozen and applied to a newly generated
Mach-3, Reynolds-number-one-million, viscous/no-model MFC sequence at 30-degree
incidence. The grid, solver commit, numerical settings, time interval, and
sampling cadence are controlled so that incidence angle is the intended flow
change.

The preregistered acceptance gates require complete finite raw fields, agreement
between written and velocity-derived vorticity, at least 0.70 total and
close-core coverage, at least 80% retention of both alpha-40 source metrics,
and no more than 1.30 detections per criteria-derived reference. No parameter
sweep is performed on the alpha-30 case.

Passing CC-FCV supports cross-case transfer within this flow family. It does
not establish publication precision or recall because the reference catalogue
is criteria-derived. Blinded expert labels and a third flow topology remain
required for a high-confidence journal claim.
