# Aircraft reference viewer

Aircraft Twin selects geometry from the actual aircraft type field: `A320-class` uses an A320-200 reference with sharklets; `B738-class` uses a 737-800 reference with blended winglets. Unknown types receive a clearly labelled generic narrow-body reference. SIM-017 is a 737 reference.

The meshes are authored locally and work offline. They are illustrative reference models, not manufacturer CAD or as-built records. Engine variants, wingtip installations and internal equipment layouts are not present in the fleet data. The UI explicitly labels that limitation.

Overall length/span references:
- Airbus A320: 37.57 / 35.80 m. [Airbus airport and maintenance planning](https://aircraft.airbus.com/sites/g/files/jlcbta126/files/2023-12/ac_a320_1223.pdf).
- Boeing 737-800: 39.5 / 35.8 m with optional winglets. [Boeing NG design highlights](https://www.boeing.com/Commercial/737ng/737-next-generation-design-highlights).

The viewer provides smooth pressure shells, surface-following windows and doors, segmented cockpit glazing, closed tapered airfoil wings, different wingtip shapes, stabilizers, fin, engine lips/fans/spinners/exhausts/reverser seams, gear struts/braces/wheels, antennas and a belly fairing. Cutaway reveals schematic compressor/turbine stages, equipment racks and hydraulic distribution.

The assembly browser groups 34 reference parts into engines, airframe, flight controls, landing gear and avionics. Geometry selections map to the existing six monitored components. Subparts never acquire fabricated individual health readings. Nose wheels map to landing gear, not brakes. Structural airframe parts remain geometry-only. Canvas picking respects the visible outer skin; hidden equipment becomes pickable in cutaway.

Warm, neutral studio lighting replaces the glowing markers and grid. Restrained selection shading, manual orbit/zoom, view presets and optional slow rotation remain. Fan animation respects reduced-motion preferences; rendering pauses in hidden tabs. WebGL fallback preserves component selection.

Validation: `npm run test:models` checks finite vertices/normals, unique part identifiers, type-specific overall dimensions, valid telemetry links and hidden internals without a browser or WebGL. `npm run build` validates TypeScript and production packaging. Browser inspection remains pending because the saved preview policy blocks localhost access.
