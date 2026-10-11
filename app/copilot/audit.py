"""Append-only audit log (CONTRACTS.md ``AuditEntry``, ``GET /audit``).

Every decision a person makes and every proposal the system drafts is recorded
here. Timestamps are simulated time, never the wall clock. Stored in the shared
``audit`` table when the database is in use (see ``persist``), else in memory.
``list_entries`` returns newest first, as the mock API does.
"""
from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Optional

from app.contracts.models import AuditEntry
from app.copilot import persist

_LOCK = RLock()
_ENTRIES: list[AuditEntry] = []


def record(actor: str, action: str, ref: str, detail: str = "", at: Optional[datetime] = None) -> AuditEntry:
    """Append one entry. ``actor`` is ``"system"``, ``"agent:<role>"`` or a person's name."""
    entry = AuditEntry(ts=at or persist.sim_now(), actor=actor, action=action, ref=ref, detail=detail)
    if persist.enabled():
        with persist.conn() as c:
            c.execute("INSERT INTO audit(ts,actor,action,ref,detail) VALUES(?,?,?,?,?)",
                      (persist.iso(entry.ts), actor, action, ref, detail))
    else:
        with _LOCK:
            _ENTRIES.append(entry)
    return entry


def list_entries(newest_first: bool = True) -> list[AuditEntry]:
    if persist.enabled():
        with persist.conn() as c:
            rows = c.execute("SELECT ts,actor,action,ref,detail FROM audit ORDER BY rowid").fetchall()
        out = [AuditEntry(ts=r["ts"], actor=r["actor"], action=r["action"], ref=r["ref"], detail=r["detail"] or "")
               for r in rows]
    else:
        with _LOCK:
            out = list(_ENTRIES)
    return list(reversed(out)) if newest_first else out


def reset() -> None:
    """Clear the in-memory log. Database rows belong to the owner of the scenario reset."""
    with _LOCK:
        _ENTRIES.clear()
