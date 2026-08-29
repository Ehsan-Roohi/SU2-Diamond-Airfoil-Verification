# Open research-workflow status

Last repository audit: 2026-08-29.  The default branch remains the public SU2
teaching package.  Every item below is a draft research branch; a green syntax
or static-test result is not a physical qualification.

| PR | Track | Recorded status | Merge gate still open |
| ---: | --- | --- | --- |
| [#1](https://github.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/pull/1) | SU2 Appendix 6A package | report-facing 0–4° Euler cases labelled `QUALIFIED_PASS`, with residual plateau disclosed | review the 105-file release/report delta and confirm that it should replace the teaching baseline |
| [#2](https://github.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/pull/2) | MFC cross-check | completed alpha-30 medium Euler run failed stationarity and shock-detection gates | longer run plus grid and far-boundary sensitivity must pass |
| [#3](https://github.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/pull/3) | Nektar++ implicit LES | staged workflow and scheduler/mesh fixes; model is ILES/DNS-style, not SST-RANS | complete solver execution and physical validation on declared profiles |
| [#4](https://github.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/pull/4) | MFC A40 startup | high-cadence diagnostic workflow | treat as diagnostic; do not infer a converged production result |
| [#5](https://github.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/pull/5) | MFC grid convergence | f270-to-f405 loads did not meet 1%/GCI stop rule; f608 is required | obtain valid f180 repair and completed f608 evidence before grid-independence claims |
| [#6](https://github.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/pull/6) | restartable A40 URANS | restart runner tested; PR explicitly makes no `QUALIFIED` claim | finish declared continuation and assess convergence/physics gates |
| [#7](https://github.com/Ehsan-Roohi/SU2-Diamond-Airfoil-Verification/pull/7) | corrected MFC 2-D immersed boundary | content-preserving relocation with planar-STL and syntax checks | run smoke and production gates in this repository; compare matched-time grids |
| pending | DART CFD-image pilot | Mach-3, alpha-40 MFC Euler and MFC viscous/no-model inputs prepared, with SU2/SST control; inference blocked by CUDA and gated SAM3 checkpoint | run on an authorized CUDA host, then score against CFD-derived reference labels |

## Repository rule

- Keep failed and incomplete evidence visible; do not merge a draft merely to
  reduce the pull-request count.
- Preserve the model distinction: Euler, laminar, SST-RANS, and implicit LES
  are not interchangeable validation targets.
- Require the exact commit, case configuration, mesh/STL digest, runtime
  environment, and machine-readable result summary for a numerical claim.
- Update this ledger when a draft is qualified, superseded, or deliberately
  archived.
