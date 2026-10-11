# AeroTwin AI web app

Vite + React + TypeScript + Tailwind + Recharts. No CDNs, fonts or map tiles, so it works offline.

```
npm install
npm run dev        # http://localhost:5173, proxies /api/* to the FastAPI server on :8000 (set API_URL to change)
npm run build      # strict TypeScript check + production build
npm run types      # regenerate src/api/schema.d.ts from the running API's OpenAPI schema (API must be up on :8000)
```

Screens: **Fleet** (KPIs, offline US map, aircraft by risk), **Planning** (Gantt of jobs vs ground windows, optimized vs baseline, "why this slot"), **Simulation Lab** (inject / delay / close / re-plan, comparison),
**Copilot drawer** (agent activity, copilot chat, work orders with approve/reject, audit). **Aircraft Twin** provides a clickable 2D aircraft, component health and RUL quantiles, predicted uncertainty band, sensor trends, quality reasons, history and timeline. The route is `/aircraft/:tail`; data comes from `GET /aircraft/{tail}` (`TwinView`).

The header shows SIMULATED always, MOCK API when `MOCK=1`, and "n/6 modules real" when some groups are still mock. Types: `src/api/types.ts` re-exports from the generated schema.

## Visual console

The operations console now uses a dark technical theme and an offline Three.js aircraft view with orbit/zoom, camera presets and a systems overlay. All health data still comes from the API. See [`../docs/VISUAL-UPGRADE.md`](../docs/VISUAL-UPGRADE.md) for features, motion/fallback behavior and verification limits.
