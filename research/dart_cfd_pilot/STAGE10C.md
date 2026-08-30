# Stage 10C: absolute scale-adaptive vortex detection

Stage 10C replaces the per-frame quantile detector with an absolute,
scale-adaptive CPU benchmark. It was developed after the Stage 10 and 10B
robustness gates failed.

The detector uses Gaussian scales of 1, 2, 4, 8, and 12 cells, absolute
swirling-strength and robust background-rotation floors, scale-dependent
Gamma2 windows, Omega-ratio and vorticity-sign coherence, scale-normalized
ranking, and sign-aware scale-dependent non-maximum suppression.

The hardened benchmark contains random and resolved close pairs, merger
configurations, an image-system wall vortex with a near-wall shear layer,
Stuart vortices embedded in shear, spatially correlated noise, core-scale
variation, and oblique shock-like irrotational clutter. Merger cases are
reported but reserved for the Stage 10D topology-event gate rather than being
forced into a two-resolved-core definition.

Parameters are selected only on the training seed. The final 240-case test
uses an independent seed and 2,000 case-bootstrap replicates.

Final frozen-test results:

- precision: 0.9553
- recall: 0.8609
- F1: 0.9056
- 95% F1 interval: [0.8822, 0.9284]
- normalized center RMSE: 0.2046 core radii
- all resolved-family recalls exceed 0.70
- claim gate: `absolute_scale_detector_pass`

This synthetic pass authorizes application of the frozen detector to the MFC
raw fields. It does not authorize claims about merge/split identity, CFD
ground truth, three-dimensional vortex tubes, or objective detection under a
time-dependent rotating observer.

Example:

```bash
python research/dart_cfd_pilot/scripts/run_vortex_stage10c_absolute_scale.py \
  --output-dir research/dart_cfd_pilot/results/stage10c \
  --train-cases 80 --test-cases 240
```
