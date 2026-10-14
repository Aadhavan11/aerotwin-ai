"""Accounts, sessions and the sign-in gate.

Off by default (nothing changes for local runs and tests). The public demo turns it on with AUTH_REQUIRED=1:
every data endpoint then needs a signed-in session, work-order decisions are limited to the roles that may make them,
and simulation controls to operations and administrators. The role a person picked (or the master account switched to) is
checked here, on the server, not just in the browser.

Storage is its own SQLite file (AUTH_DB, default data/auth.sqlite) so resetting the demo database never deletes accounts.
Passwords are hashed with scrypt. Session tokens are random and only their SHA-256 is stored. Nothing secret is logged.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app import settings

COOKIE = "aerotwin_session"

# id -> (label, one-line description). The ids are the ones the web app uses.
ROLES: dict[str, tuple[str, str]] = {
    "ops": ("Operations control", "Fleet health, decisions waiting, the plan, and the risk of waiting."),
    "maintenance": ("Maintenance planner", "Work orders to approve, technicians, parts and hangar slots."),
    "pilot": ("Pilot / flight crew", "A readiness brief per aircraft, the trips you fly and your duty time."),
    "dispatch": ("Crew & dispatch", "Departures, flight releases, crew coverage and maintenance windows."),
    "admin": ("Administrator", "Service health, data and model quality, the audit trail, scenario controls."),
}
# The six built-in accounts: username -> (role, display name). "admin" is the master account: it can switch to any role.
SEEDED: dict[str, tuple[str, str]] = {
    "operations": ("ops", "Operations control"),
    "maintenance": ("maintenance", "Maintenance planner"),
    "crew": ("pilot", "Pilot / flight crew"),
    "dispatch": ("dispatch", "Crew & dispatch"),
    "administrator": ("admin", "Administrator"),
    "admin": ("admin", "Master account"),
}
MASTER_USERNAME = "admin"

# method, path pattern, roles allowed, what it is (for the error message)
GUARDS: list[tuple[str, re.Pattern[str], set[str], str]] = [
    ("POST", re.compile(r"^/workorders/[^/]+/decision$"), {"ops", "maintenance"}, "approve or reject work orders"),
    ("PATCH", re.compile(r"^/workorders/[^/]+$"), {"ops", "maintenance"}, "edit work orders"),
    ("POST", re.compile(r"^/(sim/step|scenario/.+)$"), {"ops", "admin"}, "change the simulation"),
    ("POST", re.compile(r"^/insights/weather$"), {"ops", "admin"}, "add weather to the simulation"),
    ("DELETE", re.compile(r"^/insights/weather/[^/]+$"), {"ops", "admin"}, "remove weather from the simulation"),
]
PUBLIC_PREFIXES = ("/health", "/auth/", "/docs", "/openapi.json", "/redoc")


def enabled() -> bool:
    return settings._flag("AUTH_REQUIRED")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


DB_FILE = Path(_env("AUTH_DB", str(settings.ROOT / "data" / "auth.sqlite")))
SESSION_DAYS = int(_env("AUTH_SESSION_DAYS", "7") or 7)
_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


def db() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            DB_FILE.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                  name TEXT NOT NULL,
                  email TEXT UNIQUE COLLATE NOCASE,
                  role TEXT NOT NULL,
                  master INTEGER NOT NULL DEFAULT 0,
                  pw_hash TEXT,
                  google_sub TEXT UNIQUE,
                  picture TEXT,
                  created TEXT NOT NULL,
                  last_login TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions(
                  token_hash TEXT PRIMARY KEY,
                  user_id INTEGER NOT NULL,
                  view_role TEXT,
                  created TEXT NOT NULL,
                  expires TEXT NOT NULL
                );
                """
            )
            _conn.commit()
        return _conn


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- passwords ----------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: Optional[str]) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    try:
        _, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), digest)
    except Exception:  # noqa: BLE001
        return False


# ---------- users ----------
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,23}$")


def demo_password() -> str:
    return _env("AUTH_DEMO_PASSWORD", "aerotwin-demo") or "aerotwin-demo"


def seed() -> None:
    """Create the six built-in accounts. The five role accounts share a demo password; the master password comes from a file (never from the repo)."""
    demo = demo_password()
    c = db()
    with _lock:
        for username, (role, name) in SEEDED.items():
            row = c.execute("SELECT id, pw_hash FROM users WHERE username=?", (username,)).fetchone()
            if row is not None and username != MASTER_USERNAME and not check_password(demo, row["pw_hash"]):
                # AUTH_DEMO_PASSWORD changed since the account was seeded: re-hash so the env value is the one that works
                c.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(demo), row["id"]))
            if row is None:
                pw = None if username == MASTER_USERNAME else hash_password(demo)
                c.execute(
                    "INSERT INTO users(username,name,role,master,pw_hash,created) VALUES(?,?,?,?,?,?)",
                    (username, name, role, 1 if username == MASTER_USERNAME else 0, pw, iso(now())),
                )
        c.commit()
    _sync_master_password()


_master_mtime = 0.0


def _sync_master_password() -> None:
    """If the master password file exists (written by the key drop box), make it the master account's password. Re-read when it changes."""
    global _master_mtime
    f = _env("AUTH_MASTER_PASSWORD_FILE")
    if not f:
        return
    try:
        p = Path(f)
        m = p.stat().st_mtime
        if m == _master_mtime:
            return
        pw = p.read_text().strip()
        if len(pw) >= 8:
            with _lock:
                db().execute("UPDATE users SET pw_hash=? WHERE username=?", (hash_password(pw), MASTER_USERNAME))
                db().commit()
        _master_mtime = m
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001  never log the password or the exception text (it could echo it)
        pass


def master_emails() -> set[str]:
    return {e.strip().lower() for e in _env("AUTH_MASTER_EMAILS").split(",") if e.strip()}


def google_client_id() -> str:
    cid = _env("GOOGLE_CLIENT_ID")
    f = _env("AUTH_GOOGLE_CLIENT_ID_FILE")
    if not cid and f:
        try:
            cid = Path(f).read_text().strip()
        except Exception:  # noqa: BLE001
            cid = ""
    return cid if re.match(r"^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$", cid) else ""


def public_user(row: sqlite3.Row, view_role: Optional[str] = None) -> dict:
    master = bool(row["master"])
    role = view_role if (master and view_role in ROLES) else row["role"]
    return {
        "id": row["id"],
        "username": row["username"],
        "name": row["name"],
        "email": row["email"],
        "picture": row["picture"],
        "role": role,
        "home_role": row["role"],
        "master": master,
    }


def user_by_login(username: str) -> Optional[sqlite3.Row]:
    _sync_master_password()
    return db().execute("SELECT * FROM users WHERE username=?", (username.strip().lower(),)).fetchone()


def create_user(username: str, name: str, role: str, *, password: Optional[str] = None, email: Optional[str] = None,
                google_sub: Optional[str] = None, picture: Optional[str] = None, master: bool = False) -> sqlite3.Row:
    with _lock:
        cur = db().execute(
            "INSERT INTO users(username,name,email,role,master,pw_hash,google_sub,picture,created) VALUES(?,?,?,?,?,?,?,?,?)",
            (username, name, email, role, 1 if master else 0, hash_password(password) if password else None, google_sub, picture, iso(now())),
        )
        db().commit()
        return db().execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()


# ---------- sessions ----------
def new_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        c = db()
        c.execute("DELETE FROM sessions WHERE expires < ?", (iso(now()),))
        c.execute(
            "INSERT INTO sessions(token_hash,user_id,created,expires) VALUES(?,?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), user_id, iso(now()), iso(now() + timedelta(days=SESSION_DAYS))),
        )
        c.execute("UPDATE users SET last_login=? WHERE id=?", (iso(now()), user_id))
        c.commit()
    return token


def session_user(token: Optional[str]) -> Optional[tuple[sqlite3.Row, Optional[str]]]:
    if not token:
        return None
    with _lock:
        row = db().execute(
            "SELECT u.*, s.view_role AS view_role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires > ?",
            (hashlib.sha256(token.encode()).hexdigest(), iso(now())),
        ).fetchone()
    return (row, row["view_role"]) if row else None


def set_view_role(token: str, role: str) -> None:
    with _lock:
        db().execute("UPDATE sessions SET view_role=? WHERE token_hash=?", (role, hashlib.sha256(token.encode()).hexdigest()))
        db().commit()


def end_session(token: Optional[str]) -> None:
    if token:
        with _lock:
            db().execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
            db().commit()


# ---------- sign-in attempts ----------
_fails: dict[str, list[float]] = {}
_MAX_FAIL_KEYS = 10_000


def too_many_attempts(key: str, limit: int = 8, window: float = 600.0) -> bool:
    t = time.time()
    if key in _fails:
        _fails[key] = [x for x in _fails[key] if t - x < window]
    return len(_fails.get(key, [])) >= limit


def note_failure(key: str) -> None:
    if key not in _fails and len(_fails) >= _MAX_FAIL_KEYS:
        for old in list(_fails)[: _MAX_FAIL_KEYS // 10 or 1]:  # dicts keep insertion order: evict the oldest keys
            _fails.pop(old, None)
    _fails.setdefault(key, []).append(time.time())


def trusted_proxy_hops() -> int:
    try:
        return max(0, int(_env("TRUSTED_PROXY_HOPS", "0") or 0))
    except ValueError:
        return 0


def client_ip(peer: Optional[str], forwarded_for: str = "", cf_ip: str = "") -> str:
    """The caller's address. With TRUSTED_PROXY_HOPS=0 (default) forwarding headers are ignored and the socket peer is used.
    With N>0 the X-Forwarded-For entry N hops from the right is used (the one our own proxy appended), else the peer."""
    hops = trusted_proxy_hops()
    if hops > 0:
        parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
        if parts:
            return parts[0]
    return peer or "unknown"


# ---------- Google ----------
def verify_google(credential: str) -> dict:
    """Check a Google ID token with Google's own tokeninfo endpoint; it must be for this app's client id and have a verified email."""
    cid = google_client_id()
    if not cid:
        raise ValueError("Google sign-in is not set up on this server")
    url = "https://oauth2.googleapis.com/tokeninfo?" + urllib.parse.urlencode({"id_token": credential})
    req = urllib.request.Request(url, headers={"User-Agent": "aerotwin-auth/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            info = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        raise ValueError("Google did not accept that sign-in") from e
    if info.get("aud") != cid or info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("That sign-in was not issued for this app")
    if str(info.get("email_verified")).lower() != "true" or not info.get("email"):
        raise ValueError("Google says that email address is not verified")
    if int(info.get("exp", 0)) < time.time():
        raise ValueError("That sign-in has expired; try again")
    return info


# ---------- the gate ----------
def parse_cookie(header: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in header.decode("latin-1").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class AuthGate:
    """ASGI middleware: no session, no data. Also enforces who may approve work orders and drive the simulation."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        root = scope.get("root_path", "") or ""
        if root and (path == root or path.startswith(root + "/")):
            path = path[len(root):] or "/"  # inside a mount (app.serve puts the API at /api) the path still carries the prefix
        if scope["type"] != "http" or not enabled() or scope["method"] == "OPTIONS" or path.startswith(PUBLIC_PREFIXES):
            return await self.inner(scope, receive, send)
        cookies = parse_cookie(dict(scope["headers"]).get(b"cookie", b""))
        found = session_user(cookies.get(COOKIE))
        if not found:
            return await _json(send, 401, {"error": "auth_required", "detail": "Sign in to use AeroTwin."})
        row, view_role = found
        user = public_user(row, view_role)
        for method, pattern, roles, what in GUARDS:
            if scope["method"] == method and pattern.match(path) and user["role"] not in roles:
                label = ROLES.get(user["role"], (user["role"],))[0]
                return await _json(send, 403, {"error": "forbidden", "detail": f"The {label} role cannot {what}."})
        scope = {**scope, "state": {**scope.get("state", {}), "auth_name": user["name"], "auth_role": user["role"], "auth_username": user["username"]}}
        await self.inner(scope, receive, send)


async def _json(send, status: int, body: dict) -> None:
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data})
