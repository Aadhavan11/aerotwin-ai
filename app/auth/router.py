"""Sign-in endpoints: /auth/config, /auth/me, /auth/login, /auth/signup, /auth/google, /auth/logout, /auth/role, /auth/users."""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import settings
from app.auth import core

router = APIRouter(prefix="/auth", tags=["auth"])


class Login(BaseModel):
    username: str
    password: str


class Signup(BaseModel):
    username: str
    password: str
    name: Optional[str] = None
    role: str


class GoogleIn(BaseModel):
    credential: str
    role: Optional[str] = None


class RoleIn(BaseModel):
    role: str


def _err(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"error": "auth", "detail": detail}, status_code=status)


def _ip(request: Request) -> str:
    h = request.headers
    return h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else "?")


def _start(response: Response, row: sqlite3.Row) -> dict:
    token = core.new_session(row["id"])
    response.set_cookie(
        core.COOKIE, token, max_age=core.SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=settings._flag("AUTH_COOKIE_SECURE"), path=core._env("AUTH_COOKIE_PATH", "/") or "/",
    )
    return {"user": core.public_user(row)}


def _current(request: Request) -> Optional[tuple[sqlite3.Row, Optional[str]]]:
    return core.session_user(request.cookies.get(core.COOKIE))


@router.get("/config")
def config():
    """What the sign-in screen needs: whether sign-in is on, the Google client id (public), the positions, and the demo accounts."""
    show = settings._flag("AUTH_SHOW_DEMO", "1")
    return {
        "enabled": core.enabled(),
        "google_client_id": core.google_client_id() or None,
        "roles": [{"id": k, "label": v[0], "blurb": v[1]} for k, v in core.ROLES.items()],
        "demo_accounts": [{"username": u, "role": r, "label": n} for u, (r, n) in core.SEEDED.items() if u != core.MASTER_USERNAME] if show else [],
        "demo_password": core._env("AUTH_DEMO_PASSWORD", "aerotwin-demo") if show else None,
    }


@router.get("/me")
def me(request: Request):
    found = _current(request)
    return {"user": core.public_user(*found) if found else None}


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    key = f"{_ip(request)}|{body.username.strip().lower()}"
    if core.too_many_attempts(key) or core.too_many_attempts(_ip(request), 30):
        return _err(429, "Too many attempts. Wait a few minutes and try again.")
    row = core.user_by_login(body.username)
    if not row or not core.check_password(body.password, row["pw_hash"]):
        core.note_failure(key)
        core.note_failure(_ip(request))
        return _err(401, "That username and password do not match.")
    return _start(response, row)


@router.post("/signup")
def signup(body: Signup, request: Request, response: Response):
    username = body.username.strip().lower()
    if not core.USERNAME_RE.match(username):
        return _err(422, "Username: 3 to 24 letters, numbers, dots, dashes or underscores.")
    if len(body.password) < 8:
        return _err(422, "Password: at least 8 characters.")
    if body.role not in core.ROLES:
        return _err(422, "Choose your position.")
    if core.user_by_login(username):
        return _err(409, "That username is taken.")
    if core.too_many_attempts("signup|" + _ip(request), 10, 3600):
        return _err(429, "Too many new accounts from here. Try again later.")
    core.note_failure("signup|" + _ip(request))
    name = (body.name or "").strip()[:60] or username
    row = core.create_user(username, name, body.role, password=body.password)
    return _start(response, row)


@router.post("/google")
def google(body: GoogleIn, request: Request, response: Response):
    if core.too_many_attempts("google|" + _ip(request), 40):
        return _err(429, "Too many attempts. Wait a few minutes and try again.")
    try:
        info = core.verify_google(body.credential)
    except ValueError as e:
        core.note_failure("google|" + _ip(request))
        return _err(401, str(e))
    sub, email = info["sub"], info["email"].lower()
    name = (info.get("name") or email.split("@")[0])[:60]
    picture = info.get("picture")
    c = core.db()
    row = c.execute("SELECT * FROM users WHERE google_sub=? OR email=?", (sub, email)).fetchone()
    if row is None:
        is_master = email in core.master_emails()
        if not is_master and body.role not in core.ROLES:
            return {"needs_role": True, "email": email, "name": name, "picture": picture}
        base = re.sub(r"[^a-z0-9._-]", "", email.split("@")[0].lower()) or "user"
        base = (base if base[0].isalnum() else "u" + base)[:18].ljust(3, "0")
        username, n = base, 1
        while core.user_by_login(username):
            n += 1
            username = f"{base}{n}"
        row = core.create_user(username, name, "admin" if is_master else str(body.role), email=email, google_sub=sub, picture=picture, master=is_master)
    else:
        with core._lock:
            c.execute("UPDATE users SET google_sub=COALESCE(google_sub,?), picture=?, email=COALESCE(email,?) WHERE id=?", (sub, picture, email, row["id"]))
            if email in core.master_emails():
                c.execute("UPDATE users SET master=1 WHERE id=?", (row["id"],))
            c.commit()
        row = c.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
    return _start(response, row)


@router.post("/logout")
def logout(request: Request, response: Response):
    core.end_session(request.cookies.get(core.COOKIE))
    response.delete_cookie(core.COOKIE, path=core._env("AUTH_COOKIE_PATH", "/") or "/")
    return {"user": None}


@router.post("/role")
def switch_role(body: RoleIn, request: Request):
    """The master account views the app as any position (for demos). Everyone else keeps the position they chose."""
    found = _current(request)
    if not found:
        return _err(401, "Sign in first.")
    row, _ = found
    if not row["master"]:
        return _err(403, "Only the master account can switch positions.")
    if body.role not in core.ROLES:
        return _err(422, "Unknown position.")
    core.set_view_role(request.cookies[core.COOKIE], body.role)
    return {"user": core.public_user(row, body.role)}


@router.get("/users")
def users(request: Request):
    found = _current(request)
    if not found or not found[0]["master"]:
        return _err(403, "Master account only.")
    rows = core.db().execute("SELECT username,name,email,role,master,created,last_login FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]
