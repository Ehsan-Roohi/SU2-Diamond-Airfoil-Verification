# Stage 8: physics-consistent vortex catalogue

Stage 8 replaces the fragmented two-criterion Stage-5 reference with a
raster-independent catalogue derived from the existing 61 raw MFC states. It
does **not** rerun MFC and does not use DART predictions or rendered colours.

Each candidate core is audited using five quantities derived from velocity or
the independently written MFC vorticity:

- signed spanwise vorticity;
- two-dimensional swirling strength;
- Q criterion;
- Omega rotation-to-deformation ratio; and
- Graftieaux Gamma2.

At least three criteria must agree, including Gamma2 and either swirling
strength or positive Q. Temporal association preserves rotation sign, predicts
constant-velocity motion, allows two missing frames, penalizes abrupt strength
changes, and reports possible merge/split neighbourhoods without silently
forcing a topology.

The run also evaluates the predeclared 3 x 3 threshold grid. The gate is
fail-closed: a catalogue is not promoted unless the raw sequence is complete,
the number of identities is at most 200, median persistent-track continuity is
at least 0.80, and the relative threshold-sensitivity spread is at most 0.60.

Submit from the repository root on Unity:

```bash
sbatch --export=ALL research/dart_cfd_pilot/scripts/submit_unity_dart_stage8.sh
```

The job reuses:

```text
/project/pi_roohie_umass_edu/DART_CFD_PILOT/stage5-mfc-raw
```

Small outputs retain the shallow layout:

```text
research/dart_cfd_pilot/results/JOBID/
research/dart_cfd_pilot/results/JOBID.tar.gz
research/dart_cfd_pilot/results/JOBID.tar.gz.sha256.txt
```

The output directory contains `stage8_catalogue.csv`, `stage8_tracks.csv`,
`stage8_events.csv`, `stage8_sensitivity.csv`, `stage8_report.json`, and a
compact environment record. A nonzero exit code means a scientific gate
failed, not that the raw fields were deleted or overwritten.
