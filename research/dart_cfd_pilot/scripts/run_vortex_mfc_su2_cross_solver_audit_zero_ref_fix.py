#!/usr/bin/env python3
"""Fail-closed shim for the MFC cross-solver zero-reference-frame bug.

Stage-8 writes one CSV row per detected reference core, so a physically valid
frame with zero cores has no row in the catalogue.  The original cross-solver
runner treated every absent frame key as a corrupt catalogue.  This shim makes
only the explicitly predeclared ``allowed_empty_reference_frames`` in the case
configuration evaluate as an empty reference set; every other absent frame
still fails closed.

The detector, matching rule, thresholds, and all scoring code remain the
original frozen implementation.  The source rewrite is asserted to occur
exactly once so upstream code changes cannot silently bypass this guard.
"""
from __future__ import annotations

from pathlib import Path


ORIGINAL = Path(__file__).with_name("run_vortex_mfc_su2_cross_solver_audit.py")

OLD = '''            reference = mfc_reference.get(int(source["frame"]), [])
            if not reference:
                raise RuntimeError(
                    f"MFC reference catalogue lacks frame {int(source['frame'])}"
                )
'''

NEW = '''            frame_index = int(source["frame"])
            if frame_index in mfc_reference:
                reference = mfc_reference[frame_index]
            else:
                allowed_empty = {
                    int(value) for value in case_cfg.get("allowed_empty_reference_frames", [])
                }
                if frame_index not in allowed_empty:
                    raise RuntimeError(
                        f"MFC reference catalogue lacks frame {frame_index}"
                    )
                reference = []
'''


def main() -> None:
    source = ORIGINAL.read_text()
    count = source.count(OLD)
    if count != 1:
        raise RuntimeError(
            "zero-reference compatibility shim expected exactly one target block; "
            f"found {count}"
        )
    patched = source.replace(OLD, NEW, 1)
    namespace = {
        "__name__": "__main__",
        "__file__": str(ORIGINAL),
        "__package__": None,
        "__cached__": None,
    }
    exec(compile(patched, str(ORIGINAL), "exec"), namespace, namespace)


if __name__ == "__main__":
    main()
