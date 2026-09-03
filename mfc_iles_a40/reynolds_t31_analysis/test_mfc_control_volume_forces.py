#!/usr/bin/env python3
"""Independent tests for the control-volume force reconstruction."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np


MODULE = Path(__file__).parent / "control_volume_force_analysis" / "reconstruct_control_volume_forces.py"
SPEC = importlib.util.spec_from_file_location("cv_force", MODULE)
assert SPEC and SPEC.loader
cv_force = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cv_force
SPEC.loader.exec_module(cv_force)


def test_embedded_self_test() -> None:
    cv_force.self_test()


def test_pressure_gradient_exact_sign() -> None:
    x = np.linspace(-1.0, 2.0, 151)
    y = np.linspace(-1.0, 1.0, 121)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    zero = np.zeros_like(xx)
    state = type("State", (), {})()
    state.x, state.y = x, y
    state.rho = np.ones_like(xx)
    state.mom_x = zero.copy(); state.mom_y = zero.copy()
    state.u = zero.copy(); state.v = zero.copy()
    state.pressure = 2.0 + 0.2 * xx - 0.3 * yy
    state.tau_xx = zero.copy(); state.tau_xy = zero.copy(); state.tau_yy = zero.copy()
    cv = cv_force.CV_BY_NAME["nominal"]
    terms = cv_force.integrate_snapshot(state, cv)
    assert math.isclose(-terms["pressure_flux_x"], -0.2 * cv.area, rel_tol=1e-10, abs_tol=1e-10)
    assert math.isclose(-terms["pressure_flux_y"], 0.3 * cv.area, rel_tol=1e-10, abs_tol=1e-10)


def test_rotation_is_invertible() -> None:
    fx, fy = 1.25, -0.4
    drag, lift, _, _ = cv_force.rotate_force(fx, fy)
    alpha = math.radians(cv_force.ALPHA_DEG)
    recovered_x = drag * math.cos(alpha) - lift * math.sin(alpha)
    recovered_y = drag * math.sin(alpha) + lift * math.cos(alpha)
    assert math.isclose(recovered_x, fx, rel_tol=1e-12)
    assert math.isclose(recovered_y, fy, rel_tol=1e-12)
