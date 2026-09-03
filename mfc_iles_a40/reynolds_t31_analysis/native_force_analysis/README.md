# Native MFC force analysis for the Reynolds suite

This is a post-processing-only Unity workflow. It reads the native MFC
`restart_data/ib_state_<step>.dat` files from the completed alpha=40 cases and
extracts total immersed-boundary force. It does not submit a CFD calculation and
does not infer force from the machine-vision tensors.

## Physics and normalization

For the pinned MFC revision `0c9a1d434410175ac483b8d71646455444e3b7eb`,
each global IB record contains 20 native-endian double-precision values:

1. time;
2. force x, y, z;
3. torque x, y, z;
4. translational velocity x, y, z;
5. angular velocity x, y, z;
6. angles x, y, z;
7. centroid x, y, z;
8. radius.

The source contract is visible in
[`m_data_output.fpp`](https://github.com/MFlowCode/MFC/blob/0c9a1d434410175ac483b8d71646455444e3b7eb/src/simulation/m_data_output.fpp#L929-L1067).

The analyzer rotates Cartesian force into freestream axes at alpha=40 degrees
and uses rho=1, U=3, chord=1, and q=4.5. The result is total native `CL` and
`CD`; a pressure/viscous decomposition is not available in `ib_state` and is
therefore not fabricated.

## Quality gates

- exactly one 160-byte body record is required per global file;
- all 20 values must be finite;
- stored time must agree with global step times `dt`;
- an all-zero noninitial force history is rejected;
- duplicated restart-boundary records are compared numerically;
- missing/pruned intervals form explicit segments and are never interpolated;
- statistics require at least five samples and 90 percent time coverage.

`PASS` means every configured source and analysis window passed. `PARTIAL` is a
successful, usable extraction with missing, pruned, zero, or otherwise unusable
sources explicitly listed in the inventory. `FAILED` means that no requested
force window was usable.

## Outputs

- `TIM_COLONIUS_REYNOLDS_FORCES.png`: compact email figure;
- `TIM_COLONIUS_NATIVE_FORCES.pdf`: methods, audit, histories, and statistics;
- `native_force_raw_history.csv`: every retained native record;
- `native_force_history.csv`: merged history with explicit segment IDs;
- `native_force_summary.csv` and `.json`: window statistics;
- `native_force_source_inventory.csv`: completeness and zero/corruption audit;
- `native_force_continuity.csv`: restart-boundary audit;
- `native_force_comparisons.csv`: grid and Reynolds comparisons;
- `TIM_COLONIUS_NATIVE_FORCES_JOB<job>.zip`: shareable archive.

## Unity entry point

Run `unity_submit_native_force_analysis.sh`. It discovers the exact completed
source directories, runs a synthetic self-test, submits one small CPU
post-processing job, and prints the checker command. Results default to the
existing `/scratch4/workspace/roohie_umass_edu-mfc-a40-cv` workspace so no write
under the full `/project` Git worktree is needed.
