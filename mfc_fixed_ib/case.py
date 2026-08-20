#!/usr/bin/env python3
"""Mach-3 flow over a diamond airfoil using MFC's 2-D immersed boundary."""

import argparse
import json
import math


parser = argparse.ArgumentParser()
parser.add_argument("--mfc", type=json.loads, default="{}", metavar="DICT")
parser.add_argument("--alpha", type=float, default=40.0, help="angle of attack in degrees")
parser.add_argument("--mode", choices=("euler", "laminar"), default="euler")
parser.add_argument(
    "--grid",
    choices=("smoke", "coarse", "fine", "very-fine"),
    default="smoke",
)
parser.add_argument("--steps", type=int, default=None, help="override number of time steps")
parser.add_argument(
    "--save-every",
    type=int,
    default=None,
    help="override the snapshot interval in time steps",
)
args = parser.parse_args()

gamma = 1.4
mach = 3.0
rho_inf = 1.0
p_inf = 1.0 / gamma
a_inf = math.sqrt(gamma * p_inf / rho_inf)
alpha = math.radians(args.alpha)
u_inf = mach * a_inf * math.cos(alpha)
v_inf = mach * a_inf * math.sin(alpha)

# MFC stores one fewer index than the number of Cartesian cells in each
# direction. Fine and very-fine therefore contain 1980 x 1800 and
# 2970 x 2700 cells, respectively. Both advance to nondimensional t=13.5 and
# save 25 equally spaced snapshots (Delta t_save=0.54), so their terminal
# fields are a meaningful f180/f270 grid comparison.
grids = {
    "smoke": {
        "m": 119,
        "n": 99,
        "steps": 20,
        "save_every": 20,
        "x": (-2.0, 4.0),
        "y": (-2.5, 2.5),
    },
    "coarse": {
        "m": 659,
        "n": 599,
        "steps": 5400,
        "save_every": 225,
        "x": (-5.0, 6.0),
        "y": (-5.0, 5.0),
    },
    "fine": {
        "m": 1979,
        "n": 1799,
        "steps": 48600,
        "save_every": 1944,
        "x": (-5.0, 6.0),
        "y": (-5.0, 5.0),
    },
    "very-fine": {
        "m": 2969,
        "n": 2699,
        "steps": 72900,
        "save_every": 2916,
        "x": (-5.0, 6.0),
        "y": (-5.0, 5.0),
    },
}

grid = grids[args.grid]
steps = args.steps if args.steps is not None else grid["steps"]
save_every = args.save_every if args.save_every is not None else grid["save_every"]
if steps <= 0:
    parser.error("--steps must be positive")
if save_every <= 0:
    parser.error("--save-every must be positive")

x_beg, x_end = grid["x"]
y_beg, y_end = grid["y"]
dx = (x_end - x_beg) / (grid["m"] + 1)
dy = (y_end - y_beg) / (grid["n"] + 1)

# Conservative explicit RK3/WENO step at Mach 3 (CFL coefficient 0.20).
dt = 0.20 * min(dx, dy) / (mach * a_inf + a_inf)
viscous = args.mode == "laminar"


def boundary_pair(normal_velocity: float) -> tuple[int, int]:
    """Return beginning/end characteristic boundary conditions."""

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
    "t_step_stop": steps,
    "t_step_save": save_every,
    "num_patches": 1,
    "model_eqns": "5eq",
    "alt_soundspeed": "F",
    "num_fluids": 1,
    "mpp_lim": "F",
    "mixture_err": "T",
    "time_stepper": "rk3",
    "weno_order": 5,
    "weno_eps": 1.0e-16,
    "weno_Re_flux": "F",
    "weno_avg": "F",
    "avg_state": "arithmetic",
    "mapped_weno": "T",
    "null_weights": "F",
    "mp_weno": "F",
    "riemann_solver": "hllc",
    "wave_speeds": "direct",
    "viscous": "T" if viscous else "F",
    "bc_x%beg": bc_x_beg,
    "bc_x%end": bc_x_end,
    "bc_y%beg": bc_y_beg,
    "bc_y%end": bc_y_end,
    "ib": "T",
    "num_ibs": 1,
    "fd_order": 2,
    "num_stl_models": 1,
    "patch_ib(1)%geometry": 5,
    "patch_ib(1)%model_id": 1,
    "stl_models(1)%model_filepath": "Diamond_Airfoil_2D_MFC.stl",
    "stl_models(1)%model_threshold": 0.90,
    "patch_ib(1)%slip": "F" if viscous else "T",
    "format": "silo",
    "precision": "double",
    "prim_vars_wrt": "T",
    "ib_state_wrt": "T",
    "rho_wrt": "T",
    "pres_wrt": "T",
    "vel_wrt(1)": "T",
    "vel_wrt(2)": "T",
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
}


def add_subsonic_boundary_targets(axis: str, beginning: int, end: int) -> None:
    """Add GRCBC freestream targets for subsonic Cartesian-normal flow."""

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

if viscous:
    case["fluid_pp(1)%Re(1)"] = 1.0e6

print(json.dumps(case))
