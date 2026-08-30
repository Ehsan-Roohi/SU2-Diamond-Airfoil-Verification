# Stage 10E: physics-based close-vortex deblending

Stage 10E augments the frozen Stage 10C detector with a local one-core versus two-core Lamb–Oseen fit. A detected region is split only when the two-core model passes predeclared BIC, residual-improvement, amplitude, physical-separation, and normalized-separation gates.

The parameters were fixed from the training seed (`20260905`) and evaluated without modification on 240 unseen cases from seed `20260906`.

## Frozen test result

- overall precision: 0.9493
- overall recall: 0.9435
- overall F1: 0.9464
- merger precision: 0.9516
- merger recall: 0.9833
- merger F1: 0.9672
- normalized merger-center RMSE: 0.0640 core radii

All acceptance gates passed. The previous Stage 10C merger result was precision 0.7941, recall 0.4500, and F1 0.5745.

This remains a controlled two-dimensional synthetic validation with known centers. It does not by itself validate close-core recovery in raw MFC fields or prove three-dimensional vortex topology.

## Run

```bash
python research/dart_cfd_pilot/scripts/run_vortex_stage10e_deblend.py --output-dir stage10e_results
```
