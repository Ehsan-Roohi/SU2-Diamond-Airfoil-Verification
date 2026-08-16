#!/usr/bin/env python3
"""Restartable f608 MFC Mach-3/AoA-40 diamond-airfoil case.

The five production segments use one case definition.  Segment 1 creates the
grid and uniform initial condition.  Later segments load the grid and state
from ``restart_data/lustre_<start-step>.dat`` in the same case directory.

The target refinement ratio from f405 is 1.5, corresponding to 607.5 cells
per chord.  Integer Cartesian counts use 6682 x 6075 cells; the y spacing and
time step preserve the exact r=1.5 sequence while the x-spacing mismatch is
only 7.5e-5 in relative terms.
"""

import argparse
import json
import math


parser = argparse.ArgumentParser()
parser.add_argument("--mfc", type=json.loads, default="{}", metavar="DICT")
parser.add_argument("--start-step", type=int, default=0)
parser.add_argument("--stop-step", type=int, default=164025)
parser.add_argument("--save-every", type=int, default=6561)
args = parser.parse_args()

if args.start_step < 0:
    parser.error("--start-step must be nonnegative")
if args.stop_step <= args.start_step:
    parser.error("--stop-step must be greater than --start-step")
if args.save_every <= 0:
    parser.error("--save-every must be positive")
if args.start_step % args.save_every or args.stop_step % args.save_every:
    parser.error("segment boundaries must be divisible by --save-every")

gamma = 1.4
mach = 3.0
rho_inf = 1.0
p_inf = 1.0 / gamma
a_inf = math.sqrt(gamma * p_inf / rho_inf)
alpha = math.radians(40.0)
u_inf = mach * a_inf * math.cos(alpha)
v_inf = mach * a_inf * math.sin(alpha)

# f180/f270/f405 use r=1.5.  The next exact cells-per-chord value is
# 607.5.  MFC needs integer Cartesian counts, so use 6682 x 6075 cells:
# dx = 11/6682 and dy = 10/6075 = 1/607.5.  MFC stores maximum zero-based
# indices, hence m=6681 and n=6074.  The stable step is dt=1/12150.
m = 6681
n = 6074
x_beg, x_end = -5.0, 6.0
y_beg, y_end = -5.0, 5.0
dx = (x_end - x_beg) / (m + 1)
dy = (y_end - y_beg) / (n + 1)
dt = 0.20 * min(dx, dy) / (mach * a_inf + a_inf)


def boundary_pair(normal_velocity: float) -> tuple[int, int]:
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
restarting = args.start_step > 0

case = {
    "run_time_info": "T",
    # MFC 0c9a1d43's validator requires the bounds even with old_grid.
    "x_domain%beg": x_beg,
    "x_domain%end": x_end,
    "y_domain%beg": y_beg,
    "y_domain%end": y_end,
    "m": m,
    "n": n,
    "p": 0,
    "dt": dt,
    "t_step_start": args.start_step,
    "t_step_stop": args.stop_step,
    "t_step_save": args.save_every,
    "num_patches": 0 if restarting else 1,
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
    "viscous": "F",
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
    "patch_ib(1)%slip": "T",
    "format": "silo",
    "precision": "double",
    "prim_vars_wrt": "T",
    "ib_state_wrt": "T",
    "rho_wrt": "T",
    "pres_wrt": "T",
    "vel_wrt(1)": "T",
    "vel_wrt(2)": "T",
    "schlieren_wrt": "F",
    "parallel_io": "T",
    "fluid_pp(1)%gamma": 1.0 / (gamma - 1.0),
    "fluid_pp(1)%pi_inf": 0.0,
}

if restarting:
    case.update(
        {
            "t_step_old": 0,
            "old_ic": "T",
            "old_grid": "T",
        }
    )
else:
    case.update(
        {
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
        }
    )


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
