#!/usr/bin/env python3
"""Mach-3 flow over the SU2 verification diamond airfoil in MFC.

This is an independent-solver cross-check. MFC uses a Cartesian ghost-cell
immersed boundary, not SU2's body-fitted O-grid. MFC currently has no RANS or
SST k-omega model; ``--mode laminar`` enables constant-viscosity laminar
Navier--Stokes at Re_c=1e6 on the same sharp STL. That mode is diagnostic and
does not reproduce SU2's r_corner/c=0.001 rounded viscous geometry.
"""

import argparse
import json
import math


parser = argparse.ArgumentParser()
parser.add_argument("--mfc", type=json.loads, default="{}", metavar="DICT")
parser.add_argument("--alpha", type=float, default=20.0, help="angle of attack in degrees")
parser.add_argument("--mode", choices=("euler", "laminar"), default="euler")
parser.add_argument("--grid", choices=("smoke", "coarse", "medium"), default="smoke")
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

# The smoke grid uses a compact box for build/run checks. The longer grids use
# a wider box (5c upstream and transverse, 5c downstream of the trailing edge)
# while retaining 60 or 120 cells/chord. The physical presets advance for
# about 13.5 chord-convection lengths, approximately one complete flow-through
# along the Mach-3 freestream direction. All presets remain diagnostics, not
# wall-resolved SST or a replacement for the SU2 20c farfield study.
grids = {
    "smoke": {"m": 119, "n": 99, "steps": 20, "save_every": 10, "x": (-2.0, 4.0), "y": (-2.5, 2.5)},
    # Coarse and medium use the same physical snapshot spacing, Delta t=0.1875.
    # This gives 25 snapshots over the default t=4.5 interval on either grid.
    "coarse": {
        "m": 659,
        "n": 599,
        "steps": 5400,
        "save_every": 225,
        "x": (-5.0, 6.0),
        "y": (-5.0, 5.0),
    },
    "medium": {
        "m": 1319,
        "n": 1199,
        "steps": 10800,
        "save_every": 450,
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

# Conservative explicit step for RK3/WENO at M=3. The longer presets cover
# about one domain flow-through. Earlier
# 900/1800-step runs were shown by saved-field comparisons to remain transient.
dt = 0.20 * min(dx, dy) / (mach * a_inf + a_inf)
viscous = args.mode == "laminar"


def boundary_pair(normal_velocity: float) -> tuple[int, int]:
    """Return beginning/end CBCs for one Cartesian direction.

    Supersonic inflow/outflow is selected when the normal Mach number exceeds
    one.  At smaller normal Mach number, use prescribed subsonic inflow and
    pressure-controlled subsonic outflow.  This matters for an oblique
    freestream: at alpha=20 or 30 degrees both the left and lower boundaries
    are supersonic inflows, whereas at small incidence the transverse normal
    Mach number is subsonic.
    """

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
    "stl_models(1)%model_filepath": "Diamond_Airfoil.stl",
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
    """Add GRCBC freestream targets when a Cartesian-normal Mach is subsonic."""

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
