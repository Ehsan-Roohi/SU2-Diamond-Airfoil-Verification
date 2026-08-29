# MFC A40 HLL article diagnostics

This postprocessor operates on the completed `f270`, Mach-3, AoA-40,
viscous/no-model, WENO5-unmapped HLL archive. It does not launch another CFD
simulation.

It writes:

- full `CL`/`CD` histories plus pressure and viscous contributions;
- mean, RMS fluctuation, correlated effective sample count, and 95% mean CI;
- a detrended Hann lift spectrum, dominant frequency, and `St=f*c/U_inf`;
- leading-edge bow-shock stand-off and local shock angle histories;
- a standardized MFC/SU2/Nektar++ comparison table;
- audit JSON, CSV tables, and publication-resolution diagnostic figures.

The fixed-STL `ib_state_*.dat` records are used only if all load slots are
finite. The completed HLL archive is known to contain NaN load slots, so its
fallback is a surface-traction integral reconstructed from saved primitive
fields, cross-checked against the MFC-style volume integral. This fallback is
explicitly labelled provisional in the JSON. It must be validated against a
finite native MFC load history before the force numbers are called
article-ready.

The default statistical window is `t=3..6`, the final half of the record. The
spectrum carries an `ARTICLE_READY` flag only if it contains at least five
resolved cycles, has a distinct peak, and agrees with an autocorrelation
period. A shorter record still produces a number, but labels it preliminary.

On Unity:

```bash
bash mfc_iles_a40/hll_production_analysis/unity_submit_article_post.sh
```

If a matching Nektar++ AoA-40 standardized summary exists, pass it as
`NEKTAR_SUMMARY=/absolute/path/to/summary.json`. The current Nektar++ package in
this repository covers AoA 0/4/8 only; those results are rejected as an angle
mismatch instead of being silently compared with AoA 40.

The SU2 importer reads every `history*.csv` below the alpha-40 URANS run root,
keeps the final inner iteration for each physical step, and obtains the
convective time scale from the supplied SU2 cfg. Set `SU2_ROOT` and
`SU2_CONFIG` if their defaults do not locate the archive.
