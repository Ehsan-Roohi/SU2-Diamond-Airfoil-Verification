# Control-volume force reconstruction for the MFC Reynolds suite

This Unity workflow reconstructs total lift and drag from the original float64
MFC conservative restart fields. It exists because the completed simulations'
native `ib_state` records contain `NaN` in the in-plane `Fx/Fy` slots.

It is strictly post-processing: **no CFD job is launched**.

## Method

For a fixed rectangular control volume enclosing the immersed airfoil,

```text
F_body = -d/dt integral_CV(rho*u) dA
         - integral_boundary_CV (rho*u*u + p*I - tau).n ds
```

The analyzer evaluates the balance on compact, nominal, and wide rectangles.
The spread of their window means is reported as method sensitivity. The
pressure and viscous terms crossing those remote rectangles are balance terms,
not a surface pressure/skin-friction decomposition.

Force is rotated through the 40-degree freestream angle and normalized with
`rho_inf=1`, `U_inf=3`, `chord=1`, and `q_inf=4.5`.

## Comparison contract

- Direct Reynolds trend: `Re_c=1e4`, `5e4`, and `1e5` on f180 over `t=3--6`.
- Grid sensitivity: the two `Re_c=1e4` f180/f270 cases over `t=3--6`.
- Mature context: `Re_c=1e6` on f270 over `t=26--31`; this is not an isolated
  Reynolds comparison because both grid and time window differ.
- Temporal standard deviation is flow variability, not uncertainty.

## Outputs

- `TIM_COLONIUS_REYNOLDS_FORCE_TRENDS.png`: minimal email figure;
- `TIM_COLONIUS_CONTROL_VOLUME_FORCES.pdf`: methods/audit report;
- `control_volume_force_history.csv`: nominal-CV lift/drag time series;
- `control_volume_force_history_all.csv`: all CVs and balance terms;
- `control_volume_force_summary.csv`: means, variability, and sensitivity;
- `control_volume_force_inventory.csv`: raw coverage plus native-NaN audit;
- `control_volume_force_comparisons.csv`: explicit comparisons;
- `control_volume_force_report.json`: full metadata;
- `TIM_COLONIUS_CONTROL_VOLUME_FORCES_JOB<job>.zip`: shareable archive.

`PASS` means source coverage is complete and CV-size sensitivity is at most the
configured threshold (15% by default). `QUALIFIED` is a completed extraction
whose method sensitivity exceeds that threshold or whose usable source window
has an explicitly reported small gap. Technical/source failure remains fatal.

