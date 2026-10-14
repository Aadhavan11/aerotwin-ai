# Operations console visual upgrade

> **Partly superseded (2026-10-11).** The procedural aircraft described below and its parts explorer were removed; the viewer now uses only the third-party 737-800 model (see [`ASSETS.md`](ASSETS.md)), and the shell and styling were redesigned (see [`DESIGN.md`](DESIGN.md)). This file stays as the record of that delivery.

Branch: `aadhavan/visual-upgrade`, extending `aadhavan/aircraft-twin`.

## Delivered

- Dark operations shell with persistent navigation, simulation time, connection status and actual per-module real/mock indicators.
- Fleet network graphic using the existing station coordinates and scheduled routes. In-flight positions are interpolated from simulation time and flight schedules for aircraft reported in flight; route-dash motion is decorative. Searchable aircraft registry, live readiness dial and current status distribution.
- Procedural offline Three.js aircraft with tapered fuselage, cockpit glazing, window rows, swept wings, winglets, vertical/horizontal stabilizers, engine shells/intakes/fan blades and landing gear.
- Drag orbit, scroll zoom, perspective/top/side camera presets, opt-in auto orbit, and a systems overlay with translucent surfaces/wireframe. Health markers reflect the current component status and selection. The geometry is illustrative, not manufacturer CAD.
- Instrument-style cards and higher-contrast charts, page transitions, route flow and engine fan motion. Reduced-motion preferences suppress automatic animation. Accessible component buttons remain available; WebGL initialization failure falls back to the existing SVG twin.
- Aircraft/Planning/Lab and the Three.js scene load separately, keeping 3D out of the initial fleet page bundle. Renderer resolution is capped and rendering is limited to about 30 fps; hidden tabs skip frames. Graphics resources are disposed on unmount.

## Preview and validation

The existing development server at `http://127.0.0.1:5174` hot-reloads these changes. Open Fleet, then Aircraft Twin to inspect the 3D view. Normal setup remains `npm ci` / `npm run dev` in `web/`, with `app.main` on port 8000.

Strict TypeScript and the production Vite build pass. Vite reports the Three.js scene chunk above 500 kB (loaded only on the aircraft view; about 152 kB gzipped). Backend behavior is unchanged.

The user authorized browser inspection, but the saved browser-site permission still rejected localhost access. Visual layout, GPU rendering and browser interactions have therefore not been directly inspected in this session. No screenshots or browser-based verification are claimed.
