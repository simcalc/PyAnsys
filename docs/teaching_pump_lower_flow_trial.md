# Lower-flow classroom operating-point trial

[Run38246776372](https://github.com/simcalc/PyAnsys/actions/runs/38246776372)
completed native CFD at 90 kg/s on the 0.30 m outlet assembly. Its 51757-cell
mesh and native interface checks passed. The final20-iteration results were:

| Quantity | Actual | Required |
| --- | ---: | ---: |
| Extended assembly head | −1.0728556502 m | Positive |
| Maximum reverse flux fraction | 1.64510935% | ≤0.5% |
| Relative head span | 2.80819755% | ≤0.5% |
| Maximum initial nonlinear pressure residual | 0.00215418844 | ≤0.0001 |

The numerical reduction correctly returned exit2. This was an engineering
rejection following completed native execution. The original run and result
remain unqualified; no historical record is relabelled.

## New declared input and equation stages

This new classroom trial uses 45 kg/s at the same1450 rpm and 0.30 m duct.
It changes the operating point, without establishing BEP or a correction of
the90 kg/s performance. Improvement and convergence are not promised.

1. Inputs: mass flow45 kg/s, density998.2 kg/m³.
2. Equation: `Q = mass_flow / density`.
3. Substitution: `45 / 998.2`.
4. Result: `0.04508114606291324 m³/s = 162.29212582649 m³/h`.

The previous90 kg/s point is `324.58425165298 m³/h`. The native OpenFOAM
inlet is controlled by volumetric flow in m³/s, using actual native face area
and normals. No analytic area is substituted for native inlet geometry.

For the unchanged0.0047993 m² outlet rectangle, `Q/A = 9.3932752824 m/s`
is a nominal input reduction. The pressure-coupled outlet remains free to
produce a nonuniform profile and backflow; this number is not imposed as an
outlet velocity and no reverse flux is clipped.

## Changes and retained criteria

Complete typed source parameters declare45 kg/s. Every other geometry and
operating parameter remains unchanged. Fresh CAD, source manifests, native
mesh quality/interface proof and fresh initial fields are required. The full
reviewed90 kg/s physics inventory is verified before changing only the inlet
`volumetricFlowRate` value. An old90 kg/s source is rejected by source admission,
study construction and the distinct namespace.

SST/MRF, prescribed inlet turbulence, fluid properties,1 MPa outlet static
reference, numerical schemes/coupling,2000 iterations, final20-iteration window,
all residual/flow/head/pressure/interface limits and FREE runner resource bounds
remain mandatory. The unchanged prescribed k and omega are explicit classroom
inputs; constant turbulence intensity or measured turbulence is not claimed.

Head remains the extended assembly quantity, including duct losses. No
pump-only head correction, active-phase H0, pressure sweep, NPSHr, physical
transient, convergence study or matched restart is established by this recipe.
Main inputs, calculations and storage are separate.

## Actionable log

The reducer now explains native completion and numerical qualification
separately. It prints actual values, limits, comparison operators, availability
and reason codes. The exit status remains2 when admission does not pass; a
friendlier message never changes an authoritative result or grants acceptance.
