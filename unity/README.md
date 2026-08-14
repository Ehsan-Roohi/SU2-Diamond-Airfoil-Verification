# Unity: alpha=40 URANS SST

This entry point resumes only the validated `medium_halfdt` stage. It is locked
to SU2 8.5.0, Mach 3, alpha 40 degrees, Reynolds number 1e6, the 720x181 medium
mesh, `dt=2.5e-6 s`, a 2,000-inner-iteration first-order BDF bootstrap, and
600 inner iterations for second-order BDF production through step 12,000.

The audited steady seed and every byte of the validated time-step-664 resume
archive are versioned with the runner under `unity/assets/`. The resume ZIP is
stored as checksum-pinned Git chunks and is reassembled automatically under
project storage; no manual upload or copy is required.
The checkout, solver, logs, restarts, and checkpoints must live on Unity project
storage, not under the quota-limited home directory. Use a checkout under
`/project/pi_roohie_umass_edu/github_sync` and run:

```bash
bash unity/submit_alpha40.sh
```

The submitter validates the seed and mesh before calling `sbatch`, and returns
the existing job ID instead of starting a duplicate. The Slurm job:

- uses the `cpu` partition and one 16-core OpenMP process;
- uses an existing `SU2_CFD` or installs the checksum-pinned official SU2 8.5.0
  Linux OMP release under project storage;
- automatically resumes the validated run from time step 664 on a fresh data
  directory;
- writes a restart at every completed physical step;
- writes one rolling atomic checkpoint ZIP under project storage after every
  20-step chunk and before a wall-time requeue;
- retains the latest two BDF restart levels, exact cfg files, histories, logs,
  seed/mesh hashes, and a machine-readable manifest in each ZIP;
- deletes obsolete per-step restart files only after the new checkpoint ZIP is
  safely finalized, bounding persistent restart storage to two levels;
- stops with `FAILED_GATE` on a nonzero solver exit, NaN/Inf in reported
  metrics, any nonphysical point, a missing restart level, or changed seed;
- never advances to another matrix member and never reports `QUALIFIED`.

Current status:

```bash
python3 scripts/unity_alpha40.py status --run-root /project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data/runs/urans_alpha40/medium_halfdt
```

Slurm status and live log:

```bash
squeue --me --name=urans-a40-prj
tail -F /project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data/slurm_logs/slurm-urans-a40-prj-JOBID.out
```
