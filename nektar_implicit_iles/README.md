# Nektar++ implicit ILES: Mach-3 rounded diamond airfoil

Reproducible three-dimensional compressible ILES workflow targeting Nektar++ v5.10.0. It rebuilds the attached SU2 case at `M=3`, `Re_c=1e6`, diamond half-angle `8 deg`, and corner radius `r/c=0.001`.

On UMass Unity, the pinned one-line bootstrap command shown in `README_FA.md`
checks out this folder, installs Nektar++ 5.10 if necessary, and submits the
smoke profile through Slurm.

Run on a Slurm cluster:

```bash
bash scripts/submit.sh smoke 4
# after PASS:
bash scripts/submit.sh pilot 4
# after pilot validation:
bash scripts/submit.sh production 4
```

This is an ILES/DNS-style Navier--Stokes calculation, not an SST-RANS implementation. See `README_FA.md` and `reference/acceptance.md` before interpreting results.
