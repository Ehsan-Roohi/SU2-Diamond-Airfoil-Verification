## Scope and model

State the solver, governing/model class (Euler, laminar, RANS, ILES/DNS-style,
or MFC), case, grid, and exact baseline commit.

## Verification

- [ ] syntax/static checks
- [ ] reproducible execution record
- [ ] convergence/stationarity gate
- [ ] grid/time-step or declared sensitivity gate
- [ ] physical comparison appropriate to the same model

Do not use a syntax pass as a physics pass.  Record `draft`, `diagnostic`,
`hold`, `failed`, `qualified`, or `validated` explicitly and update
`RESEARCH_STATUS.md`.
