# Next stage: restart the verified case from t=3 to t=6

The immediate next calculation should continue the verified `f270` solution
from its existing `t=3` checkpoint.  Repeating the start-up calculation would
discard a valid state and would not answer whether the shock/shear-layer system
has reached a statistically stationary regime.

The continuation retains every stability-critical setting of the successful
calculation: `dt=1/5400`, unmapped WENO5, HLLC with direct wave speeds, fourth-
order viscous derivatives, and an immersed-boundary neighborhood radius of
four ranks.  It writes fields every `0.05` time units, post-processes only when
at least one new checkpoint exists, audits the last two states, and generates
continuation shock and vorticity movies.

## Required Unity checkpoint

By default, `unity_submit_continuation.sh` reads the final files from:

```text
/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data/runs/mfc_iles_a40_recovery/f270_dt4_20260822-194723/final_t000_t3000_w5unmapped_hllc_dt1/restart_data
```

The required files are `lustre_16200.dat`, `ib_state_16200.dat`,
`lustre_x_cb.dat`, and `lustre_y_cb.dat`.  They are copied into a new run
directory; the successful `t=0..3` directory is never modified.

## Acceptance gate at t=6

The stage passes only if:

1. the simulation reaches checkpoint `32400` (`t=6`);
2. no `ICFL`, NaN, or Inf failure occurs;
3. the final two post-processed states pass the finite-value, positivity, and
   CFL-proxy audit; and
4. `RUN_OK_CONTINUATION.txt` is produced.

If the flow statistics are still visibly evolving at `t=6`, the same restart
path should next be used for `t=6..10`.  A finer `f405` calculation should be
scheduled only after this temporal-stationarity gate, initially over a short
matched physical-time window for grid sensitivity rather than as another long
start-from-zero run.
