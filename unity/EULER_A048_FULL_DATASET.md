# Complete SU2 Euler alpha=0/4/8 dataset on Unity

This workflow reruns the common Mach-3 sharp-diamond O-grid (720x181) cases at
angles of attack 0, 4, and 8 degrees with SU2 v8.5.0. It preserves the native
inputs and all startup/final outputs rather than retaining only report figures.

This is a new common-topology, full-field capture intended for shock-detection
and machine-learning work. It is not represented as the unavailable refined
triangular-mesh archive used for the final Appendix validation study.

From a Unity login node and this repository checkout:

```bash
bash unity/submit_euler_a048_full.sh
```

The job uses one 16-core OpenMP process and runs the three cases sequentially.
It writes only to Unity project storage.  The default locations are:

- runs: `/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data/runs/euler_a048_full/`
- ZIP archives: `/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data/artifacts/euler_a048_full/`
- Slurm logs: `/project/pi_roohie_umass_edu/SU2-Diamond-Airfoil-Verification-unity-data/slurm_logs/`

Each ZIP includes the sharp mesh, both configurations, startup and second-order
restart CSVs, volume VTUs, surface CSV/VTU files when emitted, histories,
solver logs, fail-closed run manifests, native shock-ridge diagnostics,
provenance, a file inventory, and SHA-256 checksums.  Byte-completeness and
scientific acceptance are deliberately recorded separately: a nonzero
acceptance gate does not delete otherwise complete raw fields.
