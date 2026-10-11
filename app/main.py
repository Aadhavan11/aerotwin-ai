"""AeroTwin AI API. Run:  uvicorn app.main:app --reload --port 8000        (MOCK=1 to serve fixtures only)

Each module group is served by the REAL module if `app/<group>/router.py` exists and imports cleanly, otherwise by the MOCK router (app/api/mock.py).
GET /health shows which is which and any import error, so a broken module is visible instead of silently mocked.
"""
from __future__ import annotations

import importlib
import traceback

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db, registry, settings
from .api import core, mock
from .auth import core as auth_core, messages as auth_messages, router as auth_router

app = FastAPI(title="AeroTwin AI API", version=settings.VERSION,
              description="Advisory-only maintenance planning prototype on SIMULATED data. See CONTRACTS.md for the spec.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "http://127.0.0.1:4173"],
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Data-Source"])


class _NoTrailingSlash:
    """Serve `/plan/` the same as `/plan` instead of FastAPI's 307, whose Location points at the internal host and breaks fetch behind the proxy."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and len(scope["path"]) > 1 and scope["path"].endswith("/"):
            scope = {**scope, "path": scope["path"].rstrip("/") or "/"}
            if scope.get("raw_path"):
                scope["raw_path"] = scope["raw_path"].rstrip(b"/") or b"/"
        await self.inner(scope, receive, send)


app.add_middleware(auth_core.AuthGate)  # off unless AUTH_REQUIRED=1
app.add_middleware(_NoTrailingSlash)  # outermost: the gate and the routes both see `/plan`, never `/plan/`
db.init_db()
if auth_core.enabled():
    auth_core.seed()
    auth_messages.seed()
app.include_router(auth_router.router)
app.include_router(auth_messages.router)
app.include_router(core.router)

for group, (path, who) in registry.GROUPS.items():
    router = None
    if settings.mock_for(group):
        registry.MODULES[group] = "mock (forced)"
    else:
        try:
            router = importlib.import_module(path).router
            registry.MODULES[group] = "real"
        except ModuleNotFoundError as e:
            if e.name and (path.startswith(e.name) or e.name == path):
                registry.MODULES[group] = f"mock (not built yet: {path})"
            else:  # the module exists but one of ITS imports is missing: tell the owner
                registry.MODULES[group] = "mock (import error)"
                registry.ERRORS[group] = f"{type(e).__name__}: {e}"
        except Exception as e:  # noqa: BLE001
            registry.MODULES[group] = "mock (import error)"
            registry.ERRORS[group] = "".join(traceback.format_exception_only(type(e), e)).strip()
    app.include_router(router or mock.ROUTERS[group])

print("modules:", {k: v for k, v in registry.MODULES.items()}, flush=True)
for g, err in registry.ERRORS.items():
    print(f"  !! {g} failed to import ({registry.GROUPS[g][1]}): {err}", flush=True)
