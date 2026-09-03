# SU2 Mach-3 angle-of-attack campaign

This section publishes compact, auditable results for the common-grid sharp
diamond-airfoil cases at angles of attack 0, 4, and 8 degrees.  It is separate
from the MFC Reynolds-number campaign.

The Unity publisher adds one immutable campaign directory containing:

- fixed-presentation density and Mach comparisons;
- lift, drag, and residual histories;
- final-window aerodynamic statistics;
- shock-ridge tables and case metrics when the numerical gate produced them;
- solver, commit, run-status, and checksum provenance.

Large native restart CSV and VTU fields remain in the lossless Unity archive.
They are not committed to ordinary Git history.

## Scientific status

The pre-existing report-reproduction campaign provides qualified single-grid
values for 0 and 4 degrees.  The new 0/4/8 capture must preserve the independent
status of each case.  In particular, an angle-8 field that exists after a
fail-closed numerical-gate failure is useful for diagnosis or ML, but is not an
accepted validation result.  No file in this directory establishes grid
independence.

See [LATEST.md](LATEST.md) after the first Unity publication.
