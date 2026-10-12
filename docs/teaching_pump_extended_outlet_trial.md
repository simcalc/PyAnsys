# Extended outlet domain trial

This is authored classroom geometry. It is not Grundfos geometry or a
reproduction of the original Fluent pump. Main application inputs, calculations,
storage and protected source are not used or modified.

## Measured reason for the trial

[Run 38215415105](https://github.com/simcalc/PyAnsys/actions/runs/38215415105)
completed 2000 native SST/MRF iterations but did not qualify numerically.
The last 20 iterations had 3.707% reverse flux, 42.609% relative head span,
and a maximum pressure initial residual of 0.003551. The criteria remain
0.5%, 0.5% and 0.0001 respectively.

An independent inspection of the retained native fields and complete mesh
found 21 reverse-flow faces out of 144 outlet faces at iteration 2000, all
inside x = 0.1505–0.1725 m. The final reverse flux fraction is 3.68795%; this
single endpoint is distinct from the 20-iteration maximum. Rotating-wall
velocities agree with `U_abs = omega cross (Cf - origin)` within
7.33e-15 m/s, and stored relative wall flux is zero. These diagnostics establish
neither stationarity nor physical accuracy.

The exact archive identity, native face counts and Python equation stages are
retained in [the face diagnostic packet](../evidence/teaching-pump/38215415105-native-face-diagnostics.json).

## Declared change and equations

Only the authored outlet length changes, from 0.15 to 0.30 m. The pressure
boundary moves from y = 0.15 to y = 0.30 m. New CAD, both component meshes,
every-cell native quality, region checks and complete interface coverage must
be generated and independently verified before a fresh flow solve.

The rectangle remains 0.11 × 0.04363 m:

1. Inputs: width 0.11 m, height 0.04363 m, length 0.30 m.
2. Equation: `Dh = 2*w*h/(w+h)`.
3. Substitution: `2*0.11*0.04363/(0.11+0.04363)`.
4. Result: Dh ≈ 0.06248 m; nominal L/Dh ≈ 4.80. This geometric ratio does not
   establish a developed profile or predict improvement.

RPM 1450, mass flow 90 kg/s, density 998.2 kg/m³, viscosity, fluid basis,
SST/MRF model, all initial fields, all physical boundary conditions, schemes,
coupling, 2000 iterations, the 1 MPa outlet static reference and the original
acceptance limits remain unchanged. The physics input inventory is checked
against all bytes of the previous reviewed recipe. Previous geometry and
numerical admission cannot be relabelled as this distinct family.

## Head measurement boundary

`H_assembly = (pt_extended_outlet - pt_inlet)/(rho*g)` with the explicitly
declared zero elevation correction in the gravity-neglected model. Added
duct losses are included. This is the extended assembly head, not pump-only
head at the original outlet plane. No loss correction or direct same-section
head comparison with the shorter domain is supplied.

## Execution and acceptance

The manual workflow `teaching-pump-free-extended-outlet.yml` runs only on the
standard Ubuntu runner in this public repository, guarded before execution.
The flow solve remains bounded to one CPU, 4 GiB RAM, 1200 seconds, a 1 GiB
workspace and 4 MiB native logs. The original mathematical and native-data
gates remain mandatory. Native completion and numerical acceptance are
separate facts, and failed evidence is retained as data for one day.

Pressure sweep, NPSHr, moving-rotor transient, refinement and restart are not
qualified by this trial. They require their own current source-bound evidence.
