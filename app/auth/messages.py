"""Messages between signed-in people: to a position (everyone in it), to one person, or to everyone.

Stored beside the accounts (same SQLite file). Read state is kept per person, so a message is unread for each recipient until that person opens it.
Anyone signed in may write to a position or a person; only the administrator and master accounts may write to everyone.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import core

router = APIRouter(prefix="/messages", tags=["messages"])
_ready_for = None
_sent_at: dict[str, list[float]] = {}


def _db():
    global _ready_for
    c = core.db()
    if _ready_for is not c:
        with core._lock:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  sender TEXT NOT NULL, sender_name TEXT NOT NULL, sender_role TEXT NOT NULL,
                  to_kind TEXT NOT NULL, to_value TEXT NOT NULL DEFAULT '',
                  subject TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS message_reads(
                  message_id INTEGER NOT NULL, username TEXT NOT NULL, read_at TEXT NOT NULL,
                  PRIMARY KEY(message_id, username)
                );
                """
            )
            c.commit()
        _ready_for = c
    return c


def _me(request: Request) -> Optional[dict]:
    found = core.session_user(request.cookies.get(core.COOKIE))
    return core.public_user(*found) if found else None


def _addressed(me: dict) -> tuple[str, tuple]:
    return "(to_kind='all' OR (to_kind='role' AND to_value=?) OR (to_kind='user' AND to_value=?))", (me["role"], me["username"])


def _label(kind: str, value: str) -> str:
    if kind == "all":
        return "Everyone"
    if kind == "role":
        return core.ROLES.get(value, (value,))[0]
    return f"@{value}"


def _recipients(kind: str, value: str) -> list[str]:
    c = _db()
    if kind == "all":
        return [r["username"] for r in c.execute("SELECT username FROM users")]
    if kind == "role":
        return [r["username"] for r in c.execute("SELECT username FROM users WHERE role=?", (value,))]
    return [value]


def seed() -> None:
    """A few starter messages so the inbox is not empty in a demo (only when there are none)."""
    c = _db()
    if c.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]:
        return
    now = core.now()
    from datetime import timedelta

    rows = [
        ("operations", "Operations control", "ops", "role", "dispatch", "SIM-017 engine 2: borescope tonight",
         "Maintenance control is planning a borescope on SIM-017 engine 2 at DFW tonight. Check the departures that touch that window and confirm the crew change.", 95),
        ("maintenance", "Maintenance planner", "maintenance", "role", "ops", "Parts: hydraulic pump",
         "One HYD-PUMP is on its way from DFW to DEN (4.5 h). The work order for SIM-014 stays pending until it lands.", 70),
        ("administrator", "Administrator", "admin", "all", "", "Welcome to AeroTwin",
         "Everything in this system is simulated. Messages you send here reach the people you address, and each of them sees it as unread until they open it.", 30),
    ]
    for sender, name, role, kind, value, subject, body, mins in rows:
        c.execute("INSERT INTO messages(sender,sender_name,sender_role,to_kind,to_value,subject,body,created) VALUES(?,?,?,?,?,?,?,?)",
                  (sender, name, role, kind, value, subject, body, core.iso(now - timedelta(minutes=mins))))
    c.commit()


def _view(r, me: dict, sent: bool) -> dict:
    c = _db()
    out = {"id": r["id"], "from": r["sender_name"], "from_username": r["sender"], "from_role": core.ROLES.get(r["sender_role"], (r["sender_role"],))[0],
           "to": _label(r["to_kind"], r["to_value"]), "to_kind": r["to_kind"], "to_value": r["to_value"], "subject": r["subject"], "body": r["body"], "created": r["created"]}
    if sent:
        rec = _recipients(r["to_kind"], r["to_value"])
        read = {x["username"] for x in c.execute("SELECT username FROM message_reads WHERE message_id=?", (r["id"],))}
        out["recipients"] = len(rec)
        out["read_by"] = len([u for u in rec if u in read])
    else:
        out["read"] = bool(c.execute("SELECT 1 FROM message_reads WHERE message_id=? AND username=?", (r["id"], me["username"])).fetchone())
    return out


@router.get("")
def list_messages(request: Request, box: str = "inbox"):
    me = _me(request)
    if not me:
        return JSONResponse({"error": "auth_required", "detail": "Sign in to read messages."}, status_code=401)
    c = _db()
    if box == "sent":
        rows = c.execute("SELECT * FROM messages WHERE sender=? ORDER BY id DESC LIMIT 100", (me["username"],)).fetchall()
        return [_view(r, me, True) for r in rows]
    where, args = _addressed(me)
    rows = c.execute(f"SELECT * FROM messages WHERE {where} AND sender != ? ORDER BY id DESC LIMIT 100", (*args, me["username"])).fetchall()
    return [_view(r, me, False) for r in rows]


@router.get("/unread")
def unread(request: Request):
    me = _me(request)
    if not me:
        return {"count": 0}
    where, args = _addressed(me)
    n = _db().execute(
        f"SELECT COUNT(*) AS n FROM messages m WHERE {where} AND m.sender != ? AND NOT EXISTS (SELECT 1 FROM message_reads r WHERE r.message_id=m.id AND r.username=?)",
        (*args, me["username"], me["username"])).fetchone()["n"]
    return {"count": n}


@router.get("/recipients")
def recipients(request: Request):
    me = _me(request)
    if not me:
        return JSONResponse({"error": "auth_required", "detail": "Sign in first."}, status_code=401)
    users = [{"username": r["username"], "name": r["name"], "role": r["role"]} for r in _db().execute("SELECT username,name,role FROM users ORDER BY name LIMIT 300")]
    return {"roles": [{"id": k, "label": v[0]} for k, v in core.ROLES.items()], "users": users, "can_broadcast": me["role"] == "admin" or me["master"]}


class Out(BaseModel):
    to_kind: str
    to_value: str = ""
    subject: str
    body: str


@router.post("")
def send(body: Out, request: Request):
    me = _me(request)
    if not me:
        return JSONResponse({"error": "auth_required", "detail": "Sign in to send messages."}, status_code=401)
    subject, text = body.subject.strip()[:120], body.body.strip()[:2000]
    if not subject or not text:
        return JSONResponse({"error": "invalid", "detail": "Write a subject and a message."}, status_code=422)
    if body.to_kind == "all":
        if not (me["role"] == "admin" or me["master"]):
            return JSONResponse({"error": "forbidden", "detail": "Only the administrator can write to everyone."}, status_code=403)
    elif body.to_kind == "role":
        if body.to_value not in core.ROLES:
            return JSONResponse({"error": "invalid", "detail": "Unknown position."}, status_code=422)
    elif body.to_kind == "user":
        if not core.user_by_login(body.to_value):
            return JSONResponse({"error": "invalid", "detail": "No such person."}, status_code=422)
    else:
        return JSONResponse({"error": "invalid", "detail": "Choose who it is for."}, status_code=422)
    t = time.time()
    _sent_at[me["username"]] = [x for x in _sent_at.get(me["username"], []) if t - x < 60]
    if len(_sent_at[me["username"]]) >= 20:
        return JSONResponse({"error": "rate_limited", "detail": "Too many messages in a minute."}, status_code=429)
    _sent_at[me["username"]].append(t)
    c = _db()
    with core._lock:
        cur = c.execute("INSERT INTO messages(sender,sender_name,sender_role,to_kind,to_value,subject,body,created) VALUES(?,?,?,?,?,?,?,?)",
                        (me["username"], me["name"], me["home_role"] if me["master"] else me["role"], body.to_kind,
                         body.to_value if body.to_kind != "all" else "", subject, text, core.iso(core.now())))
        c.commit()
    r = c.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()
    return _view(r, me, True)


@router.post("/read-all")
def read_all(request: Request):
    me = _me(request)
    if not me:
        return {"count": 0}
    where, args = _addressed(me)
    c = _db()
    with core._lock:
        c.execute(f"INSERT OR IGNORE INTO message_reads(message_id,username,read_at) SELECT id, ?, ? FROM messages WHERE {where}", (me["username"], core.iso(core.now()), *args))
        c.commit()
    return {"count": 0}


@router.post("/{message_id}/read")
def mark_read(message_id: int, request: Request):
    me = _me(request)
    if not me:
        return JSONResponse({"error": "auth_required", "detail": "Sign in first."}, status_code=401)
    where, args = _addressed(me)
    c = _db()
    row = c.execute(f"SELECT id FROM messages WHERE id=? AND {where}", (message_id, *args)).fetchone()
    if row is None:
        return JSONResponse({"error": "not_found", "detail": "No such message for you."}, status_code=404)
    with core._lock:
        c.execute("INSERT OR IGNORE INTO message_reads(message_id,username,read_at) VALUES(?,?,?)", (message_id, me["username"], core.iso(core.now())))
        c.commit()
    return {"ok": True}
