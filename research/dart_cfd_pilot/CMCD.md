# Calibrated Multi-Criterion Core Detector (CMCD)

The preceding persistence-filter experiment increased coverage of the criteria-derived reference catalogue from 78.7% to 83.7%, but its persistent subset retained 99.2% of all candidates. Stable shear-layer structures can therefore satisfy a persistence test without being independently validated vortex cores.

CMCD replaces that ineffective persistence gate with a disjoint calibration/holdout design. Frames 1-30 select a detector configuration from a predeclared grid. Frames 31-60 remain untouched until the selected configuration is frozen. Selection jointly rewards reference-catalogue coverage and recovery of same-sign close-pair members while penalizing candidate growth.

The detector combines swirling strength, locally convected Gamma2, signed-vorticity coherence, and an absolute-scale non-maximum-suppression radius. The earlier absolute two-Gaussian split is not used because no fit on the real MFC fields reached the required BIC gain or fractional improvement.

The automatic catalogue remains a physics baseline rather than exhaustive ground truth. Passing the temporal holdout supports performance against that baseline; publication-level precision still requires independent expert labels.
