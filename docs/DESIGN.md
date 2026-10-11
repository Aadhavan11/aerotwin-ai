# Design notes: how the console should look

Black and white, lit glass. Keep to these rules when you change the UI.

- **Palette:** graphite text on frosted white glass panels over a pale grey ground, with piano-black surfaces for the top bar, card title bands, the key-figures strip, primary buttons and selected states. **No blue accent.** Amber and red appear only on things that need attention; a healthy value is neutral.
- **Surfaces:** panels are translucent glass (backdrop blur) lit from the top left: a bright top edge, a soft inner shade and a long low shadow; info tiles are slightly raised. Black surfaces use a faint top lift falling to near-black with a hairline highlight, so they read as lit rather than flat.
- **Shape:** one corner radius family (18px panels, 14px tiles, pills for controls); one status marker (a small dot); no decorative icons or colour chips.
- **Type:** the system UI font stack (SF on Apple devices) and a monospace for numbers, times and identifiers. Nothing loads from the network, so the demo runs offline.
- **Motion:** none for decoration. The only animation is the engine fan in the 3D model; `prefers-reduced-motion` and `prefers-reduced-transparency` are respected.
- **Copy:** plain, specific, content-led. Headings state the situation. American English.
- **Honesty on screen:** a "Simulated" tag is always in the top bar; part-level percentages are labelled as estimates derived from the component's sensors.
- **Where:** tokens and shared styles are in `web/src/index.css` (the last blocks win); shell, navigation and the role switcher in `web/src/App.tsx`; roles in `web/src/lib/role.tsx`; shared components in `web/src/components/ui.tsx`; the 3D geometry in `web/src/components/aircraftModels.ts`.
