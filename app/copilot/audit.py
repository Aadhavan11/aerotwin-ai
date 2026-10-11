"""Append-only audit log (CONTRACTS.md ``AuditEntry``, ``GET /audit``).

Every decision a person makes and every proposal the system drafts is recorded
here. Timestamps are simulated time (``twin.state.now()``), never the wall clock.
In memory; the API runs one process.
"""
from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Optional

from app.contracts.models import AuditEntry

_LOCK = RLock()
_ENTRIES: list[AuditEntry] = []


def _now() -> datetime:
    from app.twin import state

    return state.now()


def record(actor: str, action: str, ref: str, detail: str = "", at: Optional[datetime] = None) -> AuditEntry:
    """Append one entry. ``actor`` is ``"system"``, ``"agent:<role>"`` or a person's name."""
    entry = AuditEntry(ts=at or _now(), actor=actor, action=action, ref=ref, detail=detail)
    with _LOCK:
        _ENTRIES.append(entry)
    return entry


def list_entries() -> list[AuditEntry]:
    """Oldest first, in insertion order."""
    with _LOCK:
        return list(_ENTRIES)


def reset() -> None:
    with _LOCK:
        _ENTRIES.clear()
