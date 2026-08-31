# Stage 15: variable-Gamma shear-resistant benchmark

Stage 14 showed that calibrated Q and swirling strength are nearly identical on this two-dimensional case and that both outperform Stage 13 against the criteria-derived Stage 8 catalogue. That comparison is useful but not independent: Stage 8 itself contains gradient-based criteria.

Stage 15 adds a nonlocal velocity-geometry benchmark. It follows the literature-informed variable Gamma calculation with kernel sizes 5, 7, 9, and 11 and a fixed absolute threshold of 0.63. The standard Optimized-ASDA-style Gamma1 calculation is reported separately. A second detector, GI-VGCM, removes the local convection velocity through Gamma2, requires support at multiple stencil scales, and vetoes points outside a Q-positive and swirling-strength-positive region. This adaptation targets the high bulk velocity and strong attached shear in the Mach-3 airfoil field.

Parameters controlling candidate density are calibrated only on frames 1-30. Frames 31-60 remain the fixed temporal holdout. Stage 13 and the Stage 14 Q baseline are reproduced without retuning. Physical comparison figures are generated for frames 30, 45, and 60.

The run is a method-selection experiment. Even if GI-VGCM wins the automatic holdout comparison, publication precision and recall remain blocked until the Stage 14 blinded expert labels are completed and an independent flow case is evaluated.
