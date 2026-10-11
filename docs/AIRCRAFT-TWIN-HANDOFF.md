# Aircraft Twin and shared app handoff

Branch: `aadhavan/aircraft-twin`, based on the simulator integration branch plus Shamit's platform/web commit `03541f6`.

## Run

```sh
.venv/bin/pip install -r requirements.txt -r requirements-integration.txt
MOCK=0 .venv/bin/uvicorn app.main:app --port 8000
cd web
npm ci
npm run dev
```

Open `/aircraft/SIM-017` on the web app. The existing Fleet links also open each aircraft. All assets are local; no map tiles or remote fonts are required.

The running development preview for this chat uses `http://127.0.0.1:5174/aircraft/SIM-017` with a dedicated temporary database and backend on port 8011.

## Delivered

- Clickable and keyboard-selectable aircraft component zones with status labels and a selected outline. The lowest known conservative RUL component is selected initially; buttons provide equivalent navigation.
- Component health, anomaly score, data-quality reasons, cycles, observed flight hours and last maintenance.
- Actual p10/p50/p90 RUL values; the twin's predicted-health median and uncertainty band. Unknown predictions remain unknown.
- Up to three sensor trends on independent scales, telemetry-backed history and newest-first aircraft timeline.
- Loading, empty, error/retry and stale-refresh states; polling stays quiet while the tab is hidden. A changed aircraft route clears the old aircraft's data and ignores stale responses.
- Shared app simulator router. It uses the platform database schema (including integer telemetry/audit IDs), rather than a second standalone backend.
- Simulation Lab stepping and live technician IDs; a 36-hour delay for the measured golden demo. The 12-hour delay can finish before the maintenance window and leave the plan unchanged.
- CLI reset now persists simulator-generated fleet data; previously it called the pure generator and left an empty database.

## Validation and limits

Strict TypeScript and production Vite build pass; the combined backend suite passes all 238 tests. Backend checks cover the real shared app plus database-schema compatibility. Browser preview access was declined, so visual layout and browser interactions have not been inspected in this session. Copilot and work-order approval remain mock until their owner's real router lands; the app header exposes that module status. The component view does not claim real airworthiness or certified predictions.
