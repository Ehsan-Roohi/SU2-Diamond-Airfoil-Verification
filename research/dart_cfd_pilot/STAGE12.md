# Stage 12: persistent real-field vortex catalogue

Stage 12 addresses two concrete findings from the 61-frame Stage 11 MFC/ILES audit:

1. the frozen Stage 10C detector repeatedly saturated its internal 40-candidate cap; and
2. a single-frame candidate is not sufficient evidence for a physical vortex.

The run raises the predeclared per-frame cap to 80 while retaining the Stage 10E detector thresholds. It then constructs sign-consistent one-to-one temporal tracks with a gated Hungarian assignment. Both the unfiltered candidate catalogue and the temporally persistent subset are written, so temporal filtering cannot silently erase or manufacture evidence.

The output includes three physical vorticity-field overlays, full per-detection and per-track catalogues, Stage 8 coverage audits, and two-core fit diagnostics. Stage 8 remains a physics baseline rather than exhaustive ground truth. Passing Stage 12 therefore supports a persistent candidate catalogue, not a claim that every candidate is a validated vortex.

Unity execution:

```bash
export DART_STAGE12_MFC_CASE_DIR=/project/pi_roohie_umass_edu/DART_CFD_PILOT/stage5-mfc-raw
sbatch --export=ALL research/dart_cfd_pilot/scripts/submit_unity_dart_stage12.sh
```
