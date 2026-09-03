# Tim Colonius final Reynolds-effect figure

This plotting-only workflow combines already completed results:

- same-grid (`f180`) flow metrics over `t=3--6`;
- nominal control-volume load trends over the same window; and
- fixed-scale vorticity PNGs for all four Reynolds values.

The first panel is the `Re_c=10^4` high-viscosity control. The lower-Re wake is
a comparatively smooth, weakly wavy shear layer at `t=6`; higher-Re snapshots
show clear roll-up. `Re_c=10^6` is shown as later-time `f270` context, but is not
connected to the direct Reynolds trend. The script does not read restart files
and does not submit a Slurm job.

The ML PNG writer stores ascending physical `y` from the first image row
downward. Ordinary image viewers place row zero at the top, so the raw PNGs
look vertically inverted relative to a Cartesian plot. This script flips the
snapshots vertically for presentation only; it does not change field values or
vorticity signs. The positive-y/upper-side wake therefore appears above the
airfoil in the final figure.

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

The load panel reports absolute reconstructed `C_L` and `C_D`. Only the two
right-hand diagnostic panels use percent change from the `Re_c=10^4` reference.
