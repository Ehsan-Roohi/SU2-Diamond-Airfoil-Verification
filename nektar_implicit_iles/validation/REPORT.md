# Validation report

Validation performed for bundle 1.0.0:

- Python geometry fillet test: PASS; all eight tangent points are exactly `r/c=0.001` from their fillet centers within `1e-13`.
- Session XML parse and required implicit-3D tokens: PASS.
- Shell syntax (`bash -n`): PASS.
- Force normalization/statistics synthetic regression: PASS.
- Gmsh 4.13.1 smoke geometry and mesh generation: PASS.
- Physical groups found: curve 1 (airfoil), curve 2 (farfield), surface 100 (domain).
- Smoke 2-D quadratic mesh: 7,441 nodes; 1,512 quadratic quads, 624 quadratic triangles, and 140 quadratic boundary edges.
- Airfoil boundary resolution in smoke mesh: 108 quadratic edges.

The local validation environment did not contain a built Nektar++ executable, so `NekMesh` conversion, extrusion, and the Navier--Stokes time integration are intentionally left for the Unity smoke job. The job itself repeats structural preflight after extrusion and stops before the expensive stage if required composites are absent.
