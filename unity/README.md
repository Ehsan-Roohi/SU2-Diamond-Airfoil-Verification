# Unity: alpha=40 URANS SST

This entry point resumes only the validated `medium_halfdt` stage. It is locked
to SU2 8.5.0, Mach 3, alpha 40 degrees, Reynolds number 1e6, the 720x181 medium
mesh, `dt=2.5e-6 s`, a 2,000-inner-iteration first-order BDF bootstrap, and
600 inner iterations for second-order BDF production through step 12,000.

The audited seed archive is versioned with the runner at
`unity/assets/URANS_alpha40_seed_checkpoint_iter20000.zip`; no manual upload is
required. From a clone of this branch, run:

```bash
bash unity/submit_alpha40.sh
```

The submitter validates the seed and mesh before calling `sbatch`, and returns
the existing job ID instead of starting a duplicate. The Slurm job:

- uses the `cpu` partition and one 16-core OpenMP process;
- uses an existing `SU2_CFD` or installs the checksum-pinned official SU2 8.5.0
  Linux OMP release under `$HOME/.local/opt`;
- writes a restart at every completed physical step;
- writes an atomic checkpoint ZIP to the repository root after every 20-step
  chunk and before a wall-time requeue;
- retains the latest two BDF restart levels, exact cfg files, histories, logs,
  seed/mesh hashes, and a machine-readable manifest in each ZIP;
- stops with `FAILED_GATE` on a nonzero solver exit, NaN/Inf in reported
  metrics, any nonphysical point, a missing restart level, or changed seed;
- never advances to another matrix member and never reports `QUALIFIED`.

Current status:

```bash
python3 scripts/unity_alpha40.py status --run-root unity_runs/urans_alpha40/medium_halfdt
```

Slurm status and live log:

```bash
squeue --me --name=urans-a40-mhdt
tail -F slurm-urans-a40-mhdt-JOBID.out
```
