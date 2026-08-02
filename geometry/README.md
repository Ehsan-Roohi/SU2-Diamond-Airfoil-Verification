# Diamond-airfoil geometry

`diamond_vertices.csv` lists the nondimensional wall vertices in
counter-clockwise order. The chord is `c=1`, and the half-angle is 8 degrees,
so the mid-chord half-thickness is

```text
(t/2)/c = 0.5 tan(8 degrees) = 0.0702704174.
```

The sharp Euler mesh follows the four straight segments connecting these
vertices and therefore matches the ideal geometry used in shock–expansion
theory. The viscous O-grid family regularizes the four corners with a small
radius near `r_corner/c=0.001`. That rounding avoids singular wall-normal
spacing at no-slip corners, but it means sharp-wall theory and viscous CFD are
not geometrically identical at the vertices.

The supplied meshes are ready to run. `scripts/generate_sharp_euler_ogrid.py`
regenerates only the sharp 720 x 181 Euler mesh; it is not a general rounded-grid
generator. A new mesh study must preserve wall shape, far-field radius,
topology, boundary markers, and refinement ratio consistently across all grids.
