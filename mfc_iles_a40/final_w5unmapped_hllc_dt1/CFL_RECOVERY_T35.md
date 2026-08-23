# CFL recovery from the audited t=3.5 checkpoint

The fixed-step `t=3..6` continuation stopped at step 19146
(`t=3.545555...`) when MFC reported `ICFL=1.9253393`.  This differs from the
earlier mapped-WENO failure: no NaN or Inf was reported, the ICFL excursion was
finite, and the last complete checkpoint at `t=3.5` passed the field audit.

The audited rollback state is source step 18900 on the original
`dt=1/5400` clock.  Its density and pressure are positive, all required fields
are finite, and the audit CFL proxy is 0.353478.

`unity_submit_dt4_gate_from_t35.sh` performs a guarded recovery:

1. verifies the recovered-fields marker and the final audit row;
2. downloads and checksum-verifies the successful unmapped-WENO5/HLLC case;
3. reindexes `lustre_18900.dat` and `ib_state_18900.dat` as step 75600 for
   `dt=1/21600` (`dt_factor=4`);
4. runs only the short physical interval `t=3.5..3.7`;
5. saves every 0.025 time units and requires final checkpoint 79920; and
6. post-processes, audits, and makes movies only after at least one new valid
   checkpoint exists.

The failed continuation and the original successful `t=0..3` run are never
modified.  Production continuation toward `t=6` should be submitted only
after this gate creates `RUN_OK_CONTINUATION.txt` and its final field audit
passes.
