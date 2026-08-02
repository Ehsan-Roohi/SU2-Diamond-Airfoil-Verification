# Optional guided execution with ChatGPT Codex

This route helps a student ask Codex to inspect the repository, start a case,
monitor it, and explain why it passes or fails. It does not replace the
acceptance criteria in `EXPECTED_RESULTS.csv`.

## Connect and open the repository

1. Sign in to ChatGPT with GitHub access enabled by your institution or plan.
2. In ChatGPT settings, open **Apps/Connectors**, select GitHub, and authorize
   access to this public repository.
3. Start a Codex workspace from
   `Ehsan-Roohi/SU2-Diamond-Airfoil-Verification`, or clone the repository in
   the workspace terminal.
4. Ask Codex to verify `SU2_CFD --help`, the SU2 version, `python --version`,
   and the available CPU count before starting a physical case.

GitHub-hosted compute and ChatGPT/Codex compute are different services. A
standard public GitHub Actions runner commonly exposes four virtual CPUs, but
runner specifications can change. Codex must run `nproc` on Linux or inspect
`NUMBER_OF_PROCESSORS` on Windows and record the observed value. Connecting
GitHub does not itself guarantee four cores or an installed SU2 solver.

## Recommended first prompt

```text
Open this repository and read README.md, EXPECTED_RESULTS.csv, and
TUNING_REPORT.md. Do not change the configuration yet. Verify SU2 v8.5.0 and
the available CPU count. Run the smoke test, report its manifest, and stop if
the installation test fails. If it passes, run cases/euler_alpha0 with at most
4 OpenMP threads. Monitor the log without increasing CFL. Accept the result
only if run_case.py returns QUALIFIED_PASS or PASS and explain every warning.
Do not call an SU2 exit code of zero convergence by itself.
```

## Prompt for another angle or model

```text
Run cases/sst_alpha8 with the repository wrapper and at most 4 OpenMP threads.
Preserve old outputs, record the exact commit, mesh, cfg files, SU2 version,
thread count, final residual, residual reduction, nonphysical warnings, the
full 200-sample CL/CD window, y+, and the pass/fail manifest. EXPECTED_RESULTS
contains TBD fields for this case, so do not call it verified or a benchmark.
Return it as a teaching calculation that still needs comparison with theory
and a mesh-sensitivity study.
```

## Required checks

- both startup and second-order stages completed;
- no stale history or restart was silently reused;
- final residual and residual reduction satisfy the row policy;
- at least 200 force samples exist in the final window;
- lift and drag variation meet the applicable tolerance;
- nonphysical points and reconstructed states are within the stated limit;
- zero-incidence symmetry is checked;
- shock angle and y+ are checked whenever a limit is populated;
- no `TBD` field is replaced by a guessed value.

The wrapper returns code 2 for a failed production case. A normal SU2 exit is
not enough. Retain the manifest, logs, history, configurations, mesh name,
postprocessing commands, and Git commit SHA with the submission.
