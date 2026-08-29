# MFC viscous/no-model source

The two retained PNGs are the final `t=3` frames from
`mfc-iles-a40-initial-movie-products.zip`.

- repository branch: `agent/mfc-a40-iles-final-case`
- branch commit at extraction: `6f71c45d1223dab62dc8f65b1f05dc369ab5932e`
- MFC commit: `0c9a1d434410175ac483b8d71646455444e3b7eb`
- model: single-fluid viscous equations, molecular viscosity enabled, no
  explicit RANS or SGS model
- boundary: no-slip immersed diamond
- conditions: Mach 3, alpha 40 deg, Re_c=1e6
- grid: f270, 2969 x 2699 Cartesian cells
- numerics: RK3, fifth-order unmapped WENO, HLLC, fourth-order viscous
  derivatives
- final state: step 16200, nondimensional time 3

The bundled field audit records finite final fields and a final CFL proxy of
approximately 0.3703. This is a controlled two-dimensional ILES-like screen,
not a claim of fully resolved three-dimensional LES.
