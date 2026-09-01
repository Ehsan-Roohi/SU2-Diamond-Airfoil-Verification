# Frozen analytic positive-control benchmark

This benchmark characterizes the frozen SRA-CMCD detector without fitting any
threshold.  It includes isolated Lamb-Oseen vortices of both signs, noisy
vortices, co- and counter-rotating close pairs, a shock-vortex distance sweep,
and pure shear, strain, planar-shock, and shock-bead negative controls.

Ground-truth centres and rotation signs are analytic.  The outputs therefore
measure recall, precision, sign accuracy, localization error, close-core
resolution, and the exclusion zone imposed by the frozen shock-ridge veto.
Physical vorticity panels show truth as black plus signs, final detections as
green circles, and thermodynamic shock ridges in magenta.

The first execution is also a code-invariance audit.  In particular, it must
give symmetric decisions for clockwise and counter-clockwise isolated cores;
failure of that check is a detector defect, not a tunable threshold outcome.

The close-pair gate is evaluated separately at every declared separation at
and above the resolution threshold. Aggregating the pairs is forbidden because
it could hide a non-monotone blind spot. The audit exposed one such blind spot
at `0.16c`, caused by a fixed analysis-window edge rather than the flow; the
adaptive `Q`-island window removes that implementation artifact.

Noisy isolated cases must pass both recall and precision gates. Subordinate
velocity-noise peaks are rejected by the pressure-minimum colocation and
same-sign subordinate-peak rules rather than by increasing NMS, so resolved
close pairs remain separate.

Passing this suite is necessary but not sufficient for publication.  It is a
controlled positive/adversarial benchmark, not independent CFD validation.
The next frozen tests remain a time-resolved cylinder wake and additional
airfoil cases with blinded annotations.

Unity writes the downloadable archive directly to the repository root:

```bash
sbatch --export=ALL \
  research/dart_cfd_pilot/scripts/submit_unity_vortex_analytic_positive_control.sh
```

```text
VORTEX_ANALYTIC_PC_JOBID_COMPLETE.tar.gz
```
