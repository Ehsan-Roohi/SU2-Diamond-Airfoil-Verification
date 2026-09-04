#!/usr/bin/env python3
"""MFC 2-D compressible flow over a circular cylinder.

The default remains the published Euler/slip shock-only case.  Supplying a
positive Reynolds number enables a distinct viscous/no-slip mode intended for
shock--wake development studies.  The two modes retain identical geometry,
domain, numerics, and nondimensional freestream definitions.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass


GAMMA = 1.4
RHO_INF = 1.0
P_INF = 1.0 / GAMMA
CYLINDER_DIAMETER = 1.0
CYLINDER_RADIUS = 0.5 * CYLINDER_DIAMETER
CFL_COEFFICIENT = 0.20
VISCOUS_CFL_COEFFICIENT = 0.05
RIEMANN_SOLVERS = {"hll": 1, "hllc": 2}


@dataclass(frozen=True)
class Grid:
    m: int
    n: int
    x_beg: float
    x_end: float
    y_beg: float
    y_end: float
    cells_per_diameter: int


# MFC stores the maximum zero-based index, so the number of cells is m+1 by
# n+1.  The scientific grids share the domain used by the existing Mach-3 MFC
# airfoil sequence, which makes later ML raster comparisons unambiguous.
GRIDS = {
    "smoke": Grid(119, 99, -2.0, 4.0, -2.5, 2.5, 20),
    "f90": Grid(989, 899, -5.0, 6.0, -5.0, 5.0, 90),
    "f180": Grid(1979, 1799, -5.0, 6.0, -5.0, 5.0, 180),
    "f270": Grid(2969, 2699, -5.0, 6.0, -5.0, 5.0, 270),
}


def build_case(
    mach: float,
    grid_name: str,
    final_time: float,
    save_dt: float,
    output_format: str = "silo",
    reynolds: float = 0.0,
    start_time: float = 0.0,
    restart: bool = False,
    cfl_coefficient: float | None = None,
    riemann_solver: str = "hllc",
) -> tuple[dict[str, object], dict[str, float | int | str | bool]]:
    """Return an MFC case dictionary and deterministic run metadata."""

    if not math.isfinite(mach) or mach <= 1.0:
        raise ValueError("--mach must be finite and supersonic")
    if grid_name not in GRIDS:
        raise ValueError(f"unknown grid {grid_name!r}")
    if not math.isfinite(final_time) or final_time <= 0.0:
        raise ValueError("--final-time must be finite and positive")
    if not math.isfinite(save_dt) or save_dt <= 0.0 or save_dt > final_time:
        raise ValueError("--save-dt must be positive and no larger than --final-time")
    if output_format not in {"silo", "binary"}:
        raise ValueError("output_format must be 'silo' or 'binary'")
    if not math.isfinite(reynolds) or reynolds < 0.0:
        raise ValueError("--reynolds must be zero (Euler) or finite and positive")
    if 0.0 < reynolds < 100.0:
        raise ValueError("positive --reynolds must be at least 100")
    if not math.isfinite(start_time) or start_time < 0.0 or start_time >= final_time:
        raise ValueError("--start-time must satisfy 0 <= start_time < final_time")
    if restart != (start_time > 0.0):
        raise ValueError("restart mode requires a positive --start-time and vice versa")

    default_cfl_coefficient = (
        VISCOUS_CFL_COEFFICIENT if reynolds > 0.0 else CFL_COEFFICIENT
    )
    cfl_was_overridden = cfl_coefficient is not None
    if cfl_coefficient is None:
        cfl_coefficient = default_cfl_coefficient
    if (
        not math.isfinite(cfl_coefficient)
        or cfl_coefficient <= 0.0
        or cfl_coefficient > 0.5
    ):
        raise ValueError("--cfl-coefficient must be finite and in (0, 0.5]")
    if riemann_solver not in RIEMANN_SOLVERS:
        choices = ", ".join(sorted(RIEMANN_SOLVERS))
        raise ValueError(f"--riemann-solver must be one of: {choices}")

    grid = GRIDS[grid_name]
    dx = (grid.x_end - grid.x_beg) / (grid.m + 1)
    dy = (grid.y_end - grid.y_beg) / (grid.n + 1)
    a_inf = math.sqrt(GAMMA * P_INF / RHO_INF)
    speed_inf = mach * a_inf
    viscous = reynolds > 0.0
    dt = cfl_coefficient * min(dx, dy) / (speed_inf + a_inf)

    # Keep every requested output interval equal and make the final state a
    # saved state.  The actual times differ from the request by less than one
    # explicit time step, and are recorded in the metadata.
    save_every = max(1, round(save_dt / dt))
    save_count = max(1, round(final_time / (save_every * dt)))
    stop_step = save_count * save_every
    start_step = round(start_time / dt)
    actual_save_dt = save_every * dt
    actual_final_time = stop_step * dt
    actual_start_time = start_step * dt
    if not math.isclose(actual_start_time, start_time, rel_tol=0.0, abs_tol=1.0e-12):
        raise ValueError("--start-time must be an integer multiple of the selected dt")
    if start_step % save_every:
        raise ValueError("--start-time must lie on the requested save-time lattice")

    case: dict[str, object] = {
        "run_time_info": "T",
        "x_domain%beg": grid.x_beg,
        "x_domain%end": grid.x_end,
        "y_domain%beg": grid.y_beg,
        "y_domain%end": grid.y_end,
        "m": grid.m,
        "n": grid.n,
        "p": 0,
        "dt": dt,
        "t_step_start": start_step,
        "t_step_stop": stop_step,
        "t_step_save": save_every,
        "num_patches": 0 if restart else 1,
        "model_eqns": 2,
        "alt_soundspeed": "F",
        "num_fluids": 1,
        "mpp_lim": "F",
        "mixture_err": "T",
        "time_stepper": 3,
        "weno_order": 5,
        "weno_eps": 1.0e-16,
        "weno_Re_flux": "T" if viscous else "F",
        "weno_avg": "T",
        "avg_state": 2,
        "mapped_weno": "F",
        "null_weights": "F",
        "mp_weno": "F",
        "riemann_solver": RIEMANN_SOLVERS[riemann_solver],
        "wave_speeds": 1,
        "viscous": "T" if viscous else "F",
        "fd_order": 4,
        "bc_x%beg": -11,
        "bc_x%end": -12,
        "bc_y%beg": -6,
        "bc_y%end": -6,
        "ib": "T",
        "num_ibs": 1,
        "ib_neighborhood_radius": 4,
        "patch_ib(1)%geometry": 2,
        "patch_ib(1)%x_centroid": 0.0,
        "patch_ib(1)%y_centroid": 0.0,
        "patch_ib(1)%radius": CYLINDER_RADIUS,
        "patch_ib(1)%slip": "F" if viscous else "T",
        "format": 1 if output_format == "silo" else 2,
        "precision": 2,
        "prim_vars_wrt": "T",
        "ib_state_wrt": "T",
        "rho_wrt": "T",
        "pres_wrt": "T",
        "vel_wrt(1)": "T",
        "vel_wrt(2)": "T",
        # Vorticity is retained only as a false-positive audit channel.  This
        # Euler case provides no viscous vortex-shedding ground truth.
        "omega_wrt(3)": "T",
        "schlieren_wrt": "T",
        "schlieren_alpha(1)": 0.5,
        "schlieren_alpha(2)": 0.5,
        "parallel_io": "T",
        "fluid_pp(1)%gamma": 1.0 / (GAMMA - 1.0),
        "fluid_pp(1)%pi_inf": 0.0,
    }
    if restart:
        case.update({"t_step_old": 0, "old_ic": "T", "old_grid": "T"})
    else:
        case.update(
            {
                "patch_icpp(1)%geometry": 3,
                "patch_icpp(1)%x_centroid": 0.5 * (grid.x_beg + grid.x_end),
                "patch_icpp(1)%y_centroid": 0.5 * (grid.y_beg + grid.y_end),
                "patch_icpp(1)%length_x": grid.x_end - grid.x_beg,
                "patch_icpp(1)%length_y": grid.y_end - grid.y_beg,
                "patch_icpp(1)%vel(1)": speed_inf,
                "patch_icpp(1)%vel(2)": 0.0,
                "patch_icpp(1)%pres": P_INF,
                "patch_icpp(1)%alpha_rho(1)": RHO_INF,
                "patch_icpp(1)%alpha(1)": 1.0,
            }
        )
    if viscous:
        # MFC's Reynolds parameter is the inverse nondimensional dynamic
        # viscosity.  With rho_inf=D=a_inf=1, Re_D=rho*U*D/mu and therefore
        # 1/mu = Re_D/U_inf.
        case["fluid_pp(1)%Re(1)"] = reynolds / speed_inf
    metadata: dict[str, float | int | str | bool] = {
        "mach": mach,
        "gamma": GAMMA,
        "grid": grid_name,
        "cells_x": grid.m + 1,
        "cells_y": grid.n + 1,
        "cells_per_diameter": grid.cells_per_diameter,
        "dx_over_d": dx / CYLINDER_DIAMETER,
        "dy_over_d": dy / CYLINDER_DIAMETER,
        "cfl_coefficient": cfl_coefficient,
        "default_cfl_coefficient": default_cfl_coefficient,
        "cfl_was_overridden": cfl_was_overridden,
        "riemann_solver": riemann_solver,
        "riemann_solver_id": RIEMANN_SOLVERS[riemann_solver],
        "dt": dt,
        "requested_start_time": start_time,
        "actual_start_time": actual_start_time,
        "requested_final_time": final_time,
        "actual_final_time": actual_final_time,
        "requested_save_dt": save_dt,
        "actual_save_dt": actual_save_dt,
        "saved_states_including_initial": save_count + 1,
        "output_format": output_format,
        "reynolds_number": reynolds if viscous else "Euler",
        "physics": (
            "two-dimensional compressible Navier-Stokes; continuum; no-slip cylinder"
            if viscous
            else "two-dimensional inviscid Euler; continuum; slip cylinder"
        ),
        "label_scope": (
            "shock, vortex-core proposals, and background/other; expert review required"
            if viscous
            else "shock and background/other only; vortex is not ground truth"
        ),
    }
    return case, metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mfc", type=json.loads, default="{}", metavar="DICT")
    parser.add_argument("--mach", type=float, default=2.7)
    parser.add_argument("--grid", choices=tuple(GRIDS), default="f90")
    parser.add_argument("--final-time", type=float, default=3.0)
    parser.add_argument("--save-dt", type=float, default=0.1)
    parser.add_argument("--start-time", type=float, default=0.0)
    parser.add_argument(
        "--cfl-coefficient",
        type=float,
        default=None,
        help=(
            "explicit CFL coefficient for time-step sensitivity; defaults to "
            "0.20 for Euler and 0.05 for viscous mode"
        ),
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="load old_grid/old_ic from restart_data at --start-time",
    )
    parser.add_argument(
        "--riemann-solver",
        choices=tuple(RIEMANN_SOLVERS),
        default="hllc",
        help="approximate Riemann solver; HLL is the more dissipative control",
    )
    parser.add_argument(
        "--reynolds",
        type=float,
        default=0.0,
        help="diameter Reynolds number; zero retains the Euler/slip case",
    )
    parser.add_argument("--format", choices=("silo", "binary"), default="silo")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        case, _ = build_case(
            mach=args.mach,
            grid_name=args.grid,
            final_time=args.final_time,
            save_dt=args.save_dt,
            output_format=args.format,
            reynolds=args.reynolds,
            start_time=args.start_time,
            restart=args.restart,
            cfl_coefficient=args.cfl_coefficient,
            riemann_solver=args.riemann_solver,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(case))


if __name__ == "__main__":
    main()
