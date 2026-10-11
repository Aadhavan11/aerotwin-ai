"""Where the copilot keeps work orders and the audit log.

Two backends behind the same functions:

* **database** (when ``MOCK`` is off and the shared SQLite DB is seeded): the
  ``work_orders``, ``audit`` and ``kv`` tables from ``app/db.py``. The same
  rows survive a restart, and a scenario reset that clears those tables
  clears the copilot too.
* **memory** (tests, MOCK mode, or no database yet).

``set_persistence(True/False)`` forces one for tests; ``None`` goes back to
auto-detection.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

log = logging.getLogger(__name__)
_OVERRIDE: Optional[bool] = None

_DDL = (
    "CREATE TABLE IF NOT EXISTS work_orders(id TEXT PRIMARY KEY, status TEXT, json TEXT)",
    "CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, ref TEXT, detail TEXT)",
    "CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT)",
)


def set_persistence(value: Optional[bool]) -> None:
    global _OVERRIDE
    _OVERRIDE = value


def enabled() -> bool:
    if _OVERRIDE is not None:
        return _OVERRIDE
    try:
        from app import db, settings

        return (not settings.MOCK) and (not settings.mock_for("copilot")) and db.is_seeded()
    except Exception:
        return False


@contextmanager
def conn() -> Iterator:
    """A connection to the shared DB with the copilot tables present."""
    from app import db

    with db.conn() as c:
        for ddl in _DDL:
            c.execute(ddl)
        yield c


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def sim_now() -> datetime:
    """Simulated time: the DB clock in database mode, otherwise the twin's."""
    if enabled():
        try:
            from app import db

            with conn():            # make sure the kv table exists before reading the clock
                pass
            return db.sim_now()
        except Exception as exc:
            log.warning("db clock unavailable (%s); using the twin clock", exc)
    from app.twin import state

    return state.now()
