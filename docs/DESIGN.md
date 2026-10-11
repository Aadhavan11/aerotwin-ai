# Design notes: how the console should look

The look follows Shamit's own sites (portfolio, SB Venture): restrained, near-black, plain-spoken. It is a motif, not a copy. Keep to these rules when you change the UI.

- **Canvas:** near-black `#09090f`, one very faint static glow at the top. **No background animation, no particles, nothing that follows the mouse, no dot or grid pattern.**
- **Type:** Geist (UI and headlines, tight tracking, weight 600-650) and Geist Mono (small labels, times, numbers). Both are bundled by Vite from `@fontsource-variable/*` (OFL), so nothing loads from the network. Smallest text is 11px; no 7-9px micro-type.
- **Shape:** pills for controls (nav capsule, role selector, tabs, buttons, chips); 20px cards with a 1px hairline border; no gradients, glows or corner brackets.
- **Color:** monochrome plus one blue accent (`#4c8dff`). Color means status only (green ok, amber watch, red alert, blue in maintenance, violet AOG).
- **Copy:** plain, specific, content-led. Headlines state the situation ("24 aircraft, 1 needs attention"), not a slogan. No stat-counter strips for decoration: the facts row under a heading holds only numbers that matter on that page. American English.
- **Honesty on screen:** a "Simulated" pill is always in the top bar, and the footer says the system is advisory.
- **Where:** tokens and all shared styles are in `web/src/index.css`; shell and navigation in `web/src/App.tsx`; roles in `web/src/lib/role.tsx`; shared components in `web/src/components/ui.tsx`.
