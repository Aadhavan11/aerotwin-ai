"""Which module groups are served by the REAL module and which by the MOCK router. Filled by app/main.py at startup, shown at GET /health."""
from __future__ import annotations

MODULES: dict[str, str] = {}   # group -> "real" | "mock" | "mock (reason)"
ERRORS: dict[str, str] = {}    # group -> import/startup error text (so a broken module is visible, not silent)

# group -> (module path with a `router`, who owns it; see work/OWNERSHIP.md)
GROUPS: dict[str, tuple[str, str]] = {
    "twin": ("app.twin.router", "Aashirth: GET /aircraft/{tail}, /aircraft/{tail}/telemetry"),
    "solver": ("app.solver.router", "Pardhu: GET /plan, POST /plan/solve"),
    "whatif": ("app.whatif.router", "Pardhu: POST /plan/replan, POST /whatif/compare"),
    "sim": ("app.sim.router", "Aadhavan: POST /sim/step, /scenario/inject, /scenario/reset"),
    "agents": ("app.agents.router", "Pardhu: POST /agents/run, GET /agents/runs/{id}"),
    "copilot": ("app.copilot.router", "Aashirth: POST /copilot/chat, GET/POST /workorders, GET /audit"),
}
