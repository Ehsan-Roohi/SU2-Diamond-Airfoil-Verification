# Stage 13: temporal-holdout close-core calibration

Stage 12 increased Stage 8 coverage from 78.7% to 83.7%, but its persistent subset retained 99.2% of all candidates. Stable shear-layer structures can therefore satisfy a persistence test without being independently validated vortex cores.

Stage 13 replaces that ineffective persistence gate with a disjoint calibration/holdout design. Frames 1-30 select a detector configuration from a predeclared grid. Frames 31-60 remain untouched until the selected configuration is frozen. Selection jointly rewards Stage 8 coverage and recovery of same-sign close-pair reference members while penalizing candidate growth.

The absolute two-Gaussian Stage 10E split is not used. On the real MFC fields its synthetic thresholds were outside the observed range: no fit reached either the required BIC gain or fractional improvement. Stage 13 instead tests smaller physical NMS radii directly.

Stage 8 remains a physics baseline rather than exhaustive ground truth. Passing Stage 13 supports temporal-holdout performance against that baseline; publication-level precision still requires independent expert labels.
