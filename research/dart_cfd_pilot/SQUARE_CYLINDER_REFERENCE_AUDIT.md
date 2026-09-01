# Square-cylinder frequency-reference audit

The original Re=150 cross-geometry holdout remains failed under its immutable,
predeclared `0.10 <= St <= 0.16` gate.  No score or detector output is changed.

The gate provenance was nevertheless found to be mismatched to the precise
two-dimensional low-Reynolds-number problem.  The cited Nakagawa et al. value
was a broad/transonic square-cylinder reference, whereas a dedicated
two-dimensional Re=150 resolution study reports `St=0.160-0.162` and collates
published values `0.156-0.165`.  The 5% blockage diagnostic value
`St=0.165527344` is therefore physically plausible, but it remains a post-hoc
diagnostic and cannot rescue the original holdout.

To prevent post-hoc acceptance, the next evaluation is a new prospective
Re=100 square-cylinder case.  Before its first execution, the detector is
unchanged and all solver, reference, matching, and acceptance parameters are
frozen.  Its frequency gate is `0.155 <= St <= 0.175`, centred on the published
Re=100 value `St=0.165`.  This is a prospective cross-Re test, not a second
untouched geometry and not a cross-solver validation.

References:

- R. El Mansy et al., *Square cylinder in the interface of two
  different-velocity streams*, Appendix A, J. Fluid Mech. (2022),
  arXiv:2202.01053.  The standalone two-dimensional Re=150 table reports
  `St=0.156-0.165` across the present and cited computations.
- A. Mishra et al., *Suppression of vortex shedding using a slit through the
  square cylinder at low Reynolds number*, arXiv:2107.06171.  The unmodified
  Re=100 square cylinder reports `St=0.165`.

