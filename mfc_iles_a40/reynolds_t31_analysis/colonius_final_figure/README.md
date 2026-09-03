# Tim Colonius final Reynolds-effect figure

This plotting-only workflow combines already completed results:

- same-grid (`f180`) flow metrics over `t=3--6`;
- nominal control-volume load trends over the same window; and
- fixed-scale vorticity PNGs at `t=6`.

It intentionally excludes the `Re_c=10^6` point from the direct Reynolds trend
because that case uses `f270` and a later time window. It does not read restart
files and does not submit a Slurm job.

```bash
bash unity_make_colonius_final_figure.sh \
  /path/to/tim_colonius_control_volume_forces_* \
  /path/to/tim_colonius_quantitative_checks_* \
  /path/to/ml_dataset \
  /path/to/output
```

`IMAGE_SOURCE` may instead be `MFC_A40_CV_LITE_476.tar.gz`.

The email-ready result is `TIM_COLONIUS_REYNOLDS_EFFECT_FINAL.png`. The PDF is a
vector-quality companion. The archive contains only the figure, PDF, short
interpretation, and checksums—no training tensors or raw CFD fields.
