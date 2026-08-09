# Acceptance gates

## Gate 0: executable and mesh

- Required programs found and report Nektar++ v5.10.x.
- Mesh dimension is 3 and composites `C[1]`, `C[2]`, `C[100]`, `C[101]`, `C[103]`, and `C[104]` exist.
- `C[1]`: airfoil wall; `C[2]`: farfield; `C[100:101]`: prism/hex volumes; `C[103:104]`: periodic span planes.
- Nektar++ mesh check reports no invalid Jacobians.

## Gate 1: smoke

- Solver exits normally.
- No `nan`, `inf`, negative density, or negative pressure warning appears.
- Force file has at least 20 valid records.
- Mean density and pressure remain finite.

## Gate 2: pilot physics

- Measured leading shock angles differ from the supplied SU2 values by no more than about 1 degree.
- Spanwise velocity RMS stays above numerical roundoff after the initial seed has convected away.
- Artificial viscosity is confined predominantly to compressive shock regions; the Ducros sensor prevents broad activation in vortical regions.
- The highest resolved spanwise wavenumber band contains less than 5% of the spanwise fluctuation energy.
- The final force window spans at least five convective times and has no monotonic drift.

## Gate 3: production

- `y+ <= 1` over at least 95% of the wall and maximum `y+` is reported.
- Target guidance: `Delta x+ <= 50`, `Delta z+ <= 20`; any violation must be quantified.
- Doubling span from `Lz/c=0.1` to `0.2` changes mean `CL` and `CD` by less than 2% and does not materially change the low-frequency spectrum.
- A p/order or mesh sensitivity changes mean coefficients by less than 2%.
- Report block-averaged 95% confidence intervals based on an integral autocorrelation time, not raw-sample standard error.

The automated `PASS_FAIL.txt` checks only a subset of these gates. Shock angles, wall units, spectra, and artificial-viscosity localization require field post-processing before publication.
