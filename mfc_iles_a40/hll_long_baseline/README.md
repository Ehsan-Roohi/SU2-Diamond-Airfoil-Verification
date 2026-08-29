# MFC HLL long-baseline continuation

This workflow continues the verified fresh HLL `f270` solution from `t=6` to
`t=36`.  It deliberately retains the validated 32-rank, 96-GB, Intel
`x86_64_v4` layout and splits the continuation into six restart-gated,
five-time-unit stages.  Each stage uses the same physical timestep
`dt=1/5400` and saves fields every `0.05` time units.

The first stage emails on `BEGIN` and `FAIL`.  The stages ending at `t=21`
and `t=36` email on `END` and `FAIL`; intermediate stages email only on
failure.  All stages use `afterok` dependencies, so a failed checkpoint gate
prevents downstream execution.

Expected raw checkpoint storage is approximately 195 GB.  The workflow does
not post-process or delete checkpoints.  Article diagnostics should be run
separately at the `t=21` milestone and after `t=36`.
