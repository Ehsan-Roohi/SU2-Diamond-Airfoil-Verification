# Final result: Mach-3 diamond airfoil, alpha=40 deg, SST

The calculation used SU2 v8.5.0, compressible RANS with the SST-1994m model,
an adiabatic no-slip airfoil wall, and the 720 x 181 viscous grid. A 3000-step
first-order startup was followed by an 8000-step second-order MUSCL restart.

## Qualification result

| Quantity | Value |
|---|---:|
| Final screen iteration | 7999 |
| Final CL | 0.845403 |
| Final CD | 0.974580 |
| Mean CL, final 200 records | 0.845014 |
| CL peak-to-peak, final 200 records | 0.000785 |
| Mean CD, final 200 records | 0.976186 |
| CD peak-to-peak, final 200 records | 0.003034 |
| Final log10 RMS density residual | -3.510317 |
| Maximum nonphysical points | 0 |

The solution is classified `QUALIFIED_PASS`: its final force window is stable,
the solver exited successfully, and the archived numerical fields contain no
NaN or Inf tokens. It is not classified `CONVERGED`, because the configured
residual target of -10 was not reached. A single grid also cannot establish
grid independence.
