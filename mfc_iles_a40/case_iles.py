#!/usr/bin/env python3
"""Two-dimensional viscous no-model MFC screen at Mach 3 and AoA 40 deg.

This is an ILES-like, scale-resolving discriminator rather than a claim of a
fully resolved three-dimensional LES.  It intentionally retains the Euler
case's domain, Cartesian grids, RK3/WENO5/HLLC discretization, and immersed
diamond geometry while adding molecular viscosity and a no-slip wall.
"""

import argparse
import json
import math


parser = argparse.ArgumentParser()
parser.add_argument("--mfc", type=json.loads, default="{}", metavar="DICT")
parser.add_argument(
    "--grid", choices=("smoke", "f180", "f270", "f405"), default="f270"
)
parser.add_argument("--final-time", type=float, default=3.0)
parser.add_argument("--save-dt", type=float, default=0.05)
args = parser.parse_args()

if args.final_time <= 0.0:
    parser.error("--final-time must be positive")
if args.save_dt <= 0.0 or args.save_dt > args.final_time:
    parser.error("--save-dt must be positive and no larger than --final-time")

gamma = 1.4
mach = 3.0
alpha_deg = 40.0
re_chord = 1.0e6
chord = 1.0
rho_inf = 1.0
p_inf = 1.0 / gamma
a_inf = math.sqrt(gamma * p_inf / rho_inf)
speed_inf = mach * a_inf
alpha = math.radians(alpha_deg)
u_inf = speed_inf * math.cos(alpha)
v_inf = speed_inf * math.sin(alpha)

# MFC stores maximum zero-based indices.  The production grids exactly match
# the prior Euler f180/f270/f405 sequence.  The smoke grid is only a launch and
# output gate; it is not a scientific result.
grids = {
    "smoke": {"m": 119, "n": 99, "x": (-2.0, 4.0), "y": (-2.5, 2.5)},
    "f180": {"m": 1979, "n": 1799, "x": (-5.0, 6.0), "y": (-5.0, 5.0)},
    "f270": {"m": 2969, "n": 2699, "x": (-5.0, 6.0), "y": (-5.0, 5.0)},
    "f405": {"m": 4454, "n": 4049, "x": (-5.0, 6.0), "y": (-5.0, 5.0)},
}
grid = grids[args.grid]
x_beg, x_end = grid["x"]
y_beg, y_end = grid["y"]
dx = (x_end - x_beg) / (grid["m"] + 1)
dy = (y_end - y_beg) / (grid["n"] + 1)

# Retain the Euler run's acoustic/advective CFL coefficient.
dt = 0.20 * min(dx, dy) / (speed_inf + a_inf)
stop_step = round(args.final_time / dt)
save_every = round(args.save_dt / dt)
if not math.isclose(stop_step * dt, args.final_time, rel_tol=0.0, abs_tol=1.0e-12):
    parser.error("--final-time must be an integer multiple of this grid's dt")
if not math.isclose(save_every * dt, args.save_dt, rel_tol=0.0, abs_tol=1.0e-12):
    parser.error("--save-dt must be an integer multiple of this grid's dt")
if stop_step % save_every:
    parser.error("the final step must be divisible by the snapshot interval")

# MFC expects the reciprocal dynamic viscosity.  With rho=1, U_inf=3,
# chord=1 and Re_c=1e6, mu=3e-6 and the stored value is 1/mu=1e6/3.
inverse_mu = re_chord / (rho_inf * speed_inf * chord)


def boundary_pair(normal_velocity: float) -> tuple[int, int]:
    """Choose characteristic boundary types from the normal Mach component."""

    if abs(normal_velocity) <= 1.0e-12 * a_inf:
        return -6, -6
    if normal_velocity >= a_inf:
        return -11, -12
    if normal_velocity <= -a_inf:
        return -12, -11
    if normal_velocity >= 0.0:
        return -7, -8
    return -8, -7


bc_x_beg, bc_x_end = boundary_pair(u_inf)
bc_y_beg, bc_y_end = boundary_pair(v_inf)

case = {
    "run_time_info": "T",
    "x_domain%beg": x_beg,
    "x_domain%end": x_end,
    "y_domain%beg": y_beg,
    "y_domain%end": y_end,
    "m": grid["m"],
    "n": grid["n"],
    "p": 0,
    "dt": dt,
    "t_step_start": 0,
    "t_step_stop": stop_step,
    "t_step_save": save_every,
    "num_patches": 1,
    # MFC's documented single-fluid viscous equation set.
    "model_eqns": 2,
    "alt_soundspeed": "F",
    "num_fluids": 1,
    "mpp_lim": "F",
    "mixture_err": "T",
    "time_stepper": 3,
    "weno_order": 5,
    "weno_eps": 1.0e-16,
    "weno_Re_flux": "T",
    "weno_avg": "T",
    "avg_state": 2,
    "mapped_weno": "T",
    "null_weights": "F",
    "mp_weno": "F",
    "riemann_solver": 2,
    "wave_speeds": 1,
    "viscous": "T",
    "fd_order": 4,
    "bc_x%beg": bc_x_beg,
    "bc_x%end": bc_x_end,
    "bc_y%beg": bc_y_beg,
    "bc_y%end": bc_y_end,
    "ib": "T",
    "num_ibs": 1,
    "num_stl_models": 1,
    "patch_ib(1)%geometry": 5,
    "patch_ib(1)%model_id": 1,
    "stl_models(1)%model_filepath": "Diamond_Airfoil_2D_MFC.stl",
    "stl_models(1)%model_threshold": 0.90,
    "patch_ib(1)%slip": "F",
    "format": 1,
    "precision": 2,
    "prim_vars_wrt": "T",
    "ib_state_wrt": "T",
    "rho_wrt": "T",
    "pres_wrt": "T",
    "vel_wrt(1)": "T",
    "vel_wrt(2)": "T",
    # Store the movie variables themselves, not only restart endpoints.
    "omega_wrt(3)": "T",
    "schlieren_wrt": "T",
    "schlieren_alpha(1)": 0.5,
    "schlieren_alpha(2)": 0.5,
    "parallel_io": "T",
    "patch_icpp(1)%geometry": 3,
    "patch_icpp(1)%x_centroid": 0.5 * (x_beg + x_end),
    "patch_icpp(1)%y_centroid": 0.5 * (y_beg + y_end),
    "patch_icpp(1)%length_x": x_end - x_beg,
    "patch_icpp(1)%length_y": y_end - y_beg,
    "patch_icpp(1)%vel(1)": u_inf,
    "patch_icpp(1)%vel(2)": v_inf,
    "patch_icpp(1)%pres": p_inf,
    "patch_icpp(1)%alpha_rho(1)": rho_inf,
    "patch_icpp(1)%alpha(1)": 1.0,
    "fluid_pp(1)%gamma": 1.0 / (gamma - 1.0),
    "fluid_pp(1)%pi_inf": 0.0,
    "fluid_pp(1)%Re(1)": inverse_mu,
}


def add_subsonic_boundary_targets(axis: str, beginning: int, end: int) -> None:
    if beginning == -7 or end == -7:
        case[f"bc_{axis}%grcbc_in"] = "T"
        case[f"bc_{axis}%vel_in(1)"] = u_inf
        case[f"bc_{axis}%vel_in(2)"] = v_inf
        case[f"bc_{axis}%pres_in"] = p_inf
        case[f"bc_{axis}%alpha_rho_in(1)"] = rho_inf
        case[f"bc_{axis}%alpha_in(1)"] = 1.0
    if beginning == -8 or end == -8:
        case[f"bc_{axis}%grcbc_out"] = "T"
        case[f"bc_{axis}%pres_out"] = p_inf


add_subsonic_boundary_targets("x", bc_x_beg, bc_x_end)
add_subsonic_boundary_targets("y", bc_y_beg, bc_y_end)

print(json.dumps(case))
