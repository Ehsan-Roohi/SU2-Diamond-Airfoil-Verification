#!/usr/bin/env python3
"""JFM grid-level 3 for the MFC Mach-3/AoA-40 diamond-airfoil study.

The existing publication runs use 180 and 270 cells per chord.  This case
uses 405 cells per chord so that all three grid spacings have the same
refinement ratio, r = 1.5.  Apart from the grid, stable explicit time step,
and proportional save interval, the physical and numerical settings match the
validated f270 Euler/immersed-boundary calculation.
"""

import argparse
import json
import math


parser = argparse.ArgumentParser()
parser.add_argument("--mfc", type=json.loads, default="{}", metavar="DICT")
parser.add_argument("--steps", type=int, default=109350)
parser.add_argument("--save-every", type=int, default=4374)
args = parser.parse_args()

if args.steps <= 0:
    parser.error("--steps must be positive")
if args.save_every <= 0 or args.steps % args.save_every:
    parser.error("--save-every must be positive and divide --steps exactly")

gamma = 1.4
mach = 3.0
rho_inf = 1.0
p_inf = 1.0 / gamma
a_inf = math.sqrt(gamma * p_inf / rho_inf)
alpha = math.radians(40.0)
u_inf = mach * a_inf * math.cos(alpha)
v_inf = mach * a_inf * math.sin(alpha)

# Domain and cell counts preserve the f180/f270 Cartesian family:
#
#   f180: 1980 x 1800
#   f270: 2970 x 2700
#   f405: 4455 x 4050
#
# MFC stores the maximum zero-based indices as m and n.  Therefore, the
# f405 case uses m=4454 and n=4049.  The cell spacing is exactly 1/405 in
# both directions, dt=1/8100, and the defaults advance to t=13.5 while
# saving every Delta(t)=0.54, matching the long f180/f270 runs.
m = 4454
n = 4049
x_beg, x_end = -5.0, 6.0
y_beg, y_end = -5.0, 5.0
dx = (x_end - x_beg) / (m + 1)
dy = (y_end - y_beg) / (n + 1)
dt = 0.20 * min(dx, dy) / (mach * a_inf + a_inf)


def boundary_pair(normal_velocity: float) -> tuple[int, int]:
    """Select MFC characteristic boundaries from the normal Mach number."""

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
    "m": m,
    "n": n,
    "p": 0,
    "dt": dt,
    "t_step_start": 0,
    "t_step_stop": args.steps,
    "t_step_save": args.save_every,
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
