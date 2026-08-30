# Stage 14: fair physics baselines and blinded audit

Stage 13 passed every predeclared temporal-holdout gate, but Stage 8 is not exhaustive independent ground truth. Stage 14 therefore makes two changes required for a stronger paper.

First, Q criterion, swirling strength, and absolute-vorticity extrema receive their own calibration on frames 1-30 and are evaluated only on frames 31-60. A cross-criterion consensus is reported separately from every individual method. No baseline is deliberately left untuned.

Second, the run creates a stratified blinded audit set. It contains consensus candidates, Stage-13-only candidates, and baseline-only candidates. Each crop shows only the physical vorticity field around a neutral center marker. The hidden method key and the empty expert-label table are stored separately.

Automatic comparisons remain referenced to the Stage 8 catalogue. Publication-level precision and recall are blocked until the blind expert labels are completed.
