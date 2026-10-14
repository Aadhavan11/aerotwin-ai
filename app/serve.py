"""Production wrapper: API under /api, built web UI at /, optional basic auth and LLM rate limits.

Run:  uvicorn app.serve:app --host 0.0.0.0 --port 10000
Env:  DIST_DIR, AUTH_USER, AUTH_PASS (basic gate; off when AUTH_REQUIRED=1), LLM_RATE_PER_MIN, LLM_DAILY_CAP, TRUSTED_PROXY_HOPS.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .auth import core as auth_core
from .main import app as api_app

ROOT = Path(__file__).resolve().parent.parent
LLM_PATHS = {"/api/agents/run", "/api/copilot/chat"}
_MAX_BUCKETS = 10_000


class _Guard:
    """Pure ASGI middleware: basic auth plus per-IP and daily limits on LLM-backed POSTs. In-memory only."""

    def __init__(self, app, auth_user: str | None, auth_pass: str | None, rate_per_min: int, daily_cap: int):
        self.app = app
        self.creds = (auth_user.encode(), auth_pass.encode()) if auth_user and auth_pass else None
        self.rate = max(0, rate_per_min)
        self.daily_cap = max(0, daily_cap)
        self.buckets: dict[str, tuple[float, float]] = {}  # ip -> (tokens, last_ts)
        self.day = ""
        self.day_count = 0

    @staticmethod
    async def _send(send, status: int, body: bytes, ctype: bytes, extra: list | None = None) -> None:
        headers = [(b"content-type", ctype), (b"content-length", str(len(body)).encode())] + (extra or [])
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    def _authorized(self, headers: dict[bytes, bytes]) -> bool:
        assert self.creds is not None
        value = headers.get(b"authorization", b"")
        scheme, _, token = value.partition(b" ")
        if scheme.lower() != b"basic":
            return False
        try:
            user, sep, pw = base64.b64decode(token.strip(), validate=True).partition(b":")
        except ValueError:
            return False
        ok_user = secrets.compare_digest(user, self.creds[0])
        ok_pass = secrets.compare_digest(pw, self.creds[1])
        return bool(sep) and ok_user and ok_pass

    @staticmethod
    def _client_ip(scope, headers: dict[bytes, bytes]) -> str:
        client = scope.get("client")
        return auth_core.client_ip(client[0] if client else None, headers.get(b"x-forwarded-for", b"").decode("latin-1"))

    def _allow_llm(self, ip: str) -> bool:
        now = time.time()
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        if today != self.day:
            self.day, self.day_count = today, 0
        if self.rate <= 0 or self.day_count >= self.daily_cap:
            return False
        tokens, last = self.buckets.get(ip, (float(self.rate), now))
        tokens = min(float(self.rate), tokens + (now - last) * self.rate / 60.0)
        if tokens < 1.0:
            self.buckets[ip] = (tokens, now)
            return False
        if len(self.buckets) >= _MAX_BUCKETS and ip not in self.buckets:
            self.buckets.clear()
        self.buckets[ip] = (tokens - 1.0, now)
        self.day_count += 1
        return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket" and self.creds is not None and not auth_core.enabled():
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        headers = dict(scope.get("headers") or [])
        exempt = method in ("GET", "HEAD") and path == "/api/health"
        if self.creds is not None and not auth_core.enabled() and not exempt and not self._authorized(headers):
            await self._send(send, 401, b"Authentication required", b"text/plain; charset=utf-8",
                             [(b"www-authenticate", b'Basic realm="AeroTwin"')])
            return
        if method == "POST" and path.rstrip("/") in LLM_PATHS and not self._allow_llm(self._client_ip(scope, headers)):
            body = json.dumps({"detail": "Rate limit reached for AI requests. Please wait a minute and try again."}).encode()
            await self._send(send, 429, body, b"application/json", [(b"retry-after", b"60")])
            return
        await self.app(scope, receive, send)


def create_app(dist_dir: str | Path, auth_user: str | None = None, auth_pass: str | None = None,
               llm_rate_per_min: int = 6, llm_daily_cap: int = 200) -> _Guard:
    """Build the ASGI app: API at /api, static UI from `dist_dir` with SPA fallback."""
    dist = Path(dist_dir).resolve()
    index = dist / "index.html"
    inner = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    inner.mount("/api", api_app)  # first, so unknown /api/* gets the API's JSON 404
    if (dist / "assets").is_dir():
        inner.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @inner.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if not index.is_file():
            return PlainTextResponse("Web UI is not built (missing web/dist). The API is at /api/docs.", status_code=503)
        if path:
            try:
                candidate = (dist / path).resolve()
            except (OSError, ValueError):
                candidate = None
            if candidate is not None and candidate.is_file() and candidate.is_relative_to(dist):
                return FileResponse(candidate)
        return FileResponse(index)

    return _Guard(inner, auth_user, auth_pass, llm_rate_per_min, llm_daily_cap)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


app = create_app(
    os.environ.get("DIST_DIR") or ROOT / "web" / "dist",
    os.environ.get("AUTH_USER") or None,
    os.environ.get("AUTH_PASS") or None,
    _int_env("LLM_RATE_PER_MIN", 6),
    _int_env("LLM_DAILY_CAP", 200),
)
