"""Persisted, simplified fictional MEL deferral register.

This is NOT an MMEL/MEL and is not regulatory guidance. Every payload is
simulated. Category A uses an explicit limit; expiry is inclusive at the deadline.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator, Literal, Self

from pydantic import BaseModel, Field, model_validator

from app import db
from app.copilot import audit, persist
from app.sim.v2.maintenance import MEL_INTERVAL_DAYS
from app.taxonomy.ata import ata_for

Category = Literal["A", "B", "C", "D"]
Status = Literal["open", "rectified", "expired"]
EXTENSION_DAYS: dict[str, int] = {"B": 1, "C": 5}
_DDL = "CREATE TABLE IF NOT EXISTS mel_items(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, status TEXT, json TEXT)"


class MelItem(BaseModel):
    id: str
    tail: str
    ata_chapter: str
    ata_section: str
    title: str
    category: Category
    reason: str
    deferred_at: datetime
    expires_at: datetime
    limit_days: int | None = Field(default=None, gt=0, strict=True)
    status: Status = "open"
    restrictions: list[str]
    rectified_at: datetime | None = None
    deferred_by: str
    notes: str = ""
    extensions_used: int = Field(default=0, ge=0, le=1)
    simulated: Literal[True] = True

    @model_validator(mode="after")
    def require_a_limit(self) -> Self:
        if self.category == "A" and self.limit_days is None:
            raise ValueError("category A requires a positive limit_days")
        return self


class MelList(BaseModel):
    items: list[MelItem]
    simulated: Literal[True] = True


class MelSummary(BaseModel):
    by_category: dict[str, int]
    expiring_within_72h: int
    aircraft_with_open_restrictions: list[str]
    simulated: Literal[True] = True


@dataclass(frozen=True)
class MelTemplate:
    ata_chapter: str
    ata_section: str
    category: Category
    title: str
    restrictions: tuple[str, ...]


def _template(kind: str, category: Category, title: str, restriction: str, mode: str | None = None) -> MelTemplate:
    ata = ata_for(kind, mode)
    return MelTemplate(ata.chapter, ata.section, category, title, (restriction,))


CATALOGUE: dict[tuple[str, str], MelTemplate] = {
    ("apu", "failed-to-start"): _template("apu", "B", "APU inoperative: ground power unit required at turn",
                                         "APU inoperative: ground power unit required at turn", "starter_failure"),
    ("ecs pack 1", "fault"): _template("ecs", "C", "ECS pack 1 inoperative", "ECS pack 1 inoperative: max FL 310"),
    ("anti-skid", "fault"): _template("brakes", "C", "Anti-skid inoperative", "Anti-skid inoperative: contaminated-runway limits"),
    ("avionics", "fault"): _template("avionics", "A", "Navigation display inoperative", "Navigation display inoperative: alternate display required"),
    ("electrical", "fault"): _template("electrical", "C", "Generator inoperative", "Generator inoperative: reduced electrical load, no ETOPS"),
    ("tires", "fault"): _template("tires", "D", "Tire pressure monitor inoperative", "Tire pressure monitor inoperative: manual checks each transit"),
}


class MelError(ValueError):
    """A missing item or a forbidden transition (HTTP 404 or 409)."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    # Like copilot.persist, also work before the full database has been seeded.
    with db.conn() as c:
        c.execute(_DDL)
        yield c


def _save(c: sqlite3.Connection, item: MelItem) -> None:
    c.execute("UPDATE mel_items SET status=?,json=? WHERE id=?",
              (item.status, item.model_dump_json(), item.id))


def _get(c: sqlite3.Connection, item_id: str) -> MelItem:
    row = c.execute("SELECT json FROM mel_items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise MelError(f"MEL item {item_id!r} not found", 404)
    return MelItem.model_validate_json(row["json"])


def _period(category: Category, limit_days: int | None) -> int:
    if category == "A":
        if limit_days is None or isinstance(limit_days, bool) or not isinstance(limit_days, int) or limit_days <= 0:
            raise ValueError("category A requires a positive integer limit_days")
        return limit_days
    return int(MEL_INTERVAL_DAYS[category])


def create_item(tail: str, component_kind: str, fault_mode: str, reason: str, deferred_by: str,
                category: Category | None = None, limit_days: int | None = None, notes: str = "") -> MelItem:
    """Create a catalogue deferral at simulated time and audit the named actor."""
    key = (component_kind.strip().lower(), fault_mode.strip().lower())
    template = CATALOGUE.get(key)
    if template is None:
        raise ValueError(f"unknown MEL template: {key!r}")
    if category is not None and category != template.category:
        raise ValueError(f"category must match template category {template.category}")
    if not tail.strip() or not reason.strip() or not deferred_by.strip():
        raise ValueError("tail, reason and deferred_by must be non-empty")
    now = persist.sim_now()
    item = MelItem(id="", tail=tail.strip(), ata_chapter=template.ata_chapter, ata_section=template.ata_section,
                   title=template.title, category=template.category, reason=reason.strip(), deferred_at=now,
                   expires_at=now + timedelta(days=_period(template.category, limit_days)), limit_days=limit_days,
                   restrictions=list(template.restrictions), deferred_by=deferred_by, notes=notes)
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        cursor = c.execute("INSERT INTO mel_items(status,json) VALUES(?,?)", (item.status, item.model_dump_json()))
        item.id = f"MEL-{cursor.lastrowid:04d}"
        c.execute("UPDATE mel_items SET id=?,json=? WHERE seq=?", (item.id, item.model_dump_json(), cursor.lastrowid))
    audit.record(deferred_by, "mel_created", item.id, item.reason, now)
    return item


def refresh_statuses(now: datetime | None = None) -> None:
    """Persist open -> expired when now >= expires_at; never reopen items."""
    when = now if now is not None else persist.sim_now()
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute("SELECT json FROM mel_items WHERE status='open'").fetchall()
        for row in rows:
            item = MelItem.model_validate_json(row["json"])
            if when >= item.expires_at:
                item.status = "expired"
                _save(c, item)


def list_items(tail: str | None = None, status: Status | None = None) -> list[MelItem]:
    """List refreshed items in identifier order, with optional filters."""
    refresh_statuses()
    with _conn() as c:
        rows = c.execute("SELECT json FROM mel_items ORDER BY seq").fetchall()
    items = [MelItem.model_validate_json(row["json"]) for row in rows]
    return [item for item in items if (tail is None or item.tail == tail) and (status is None or item.status == status)]


def get_item(item_id: str) -> MelItem:
    """Read an item, persisting any elapsed expiry first."""
    refresh_statuses()
    with _conn() as c:
        return _get(c, item_id)


def _require_open(item: MelItem) -> None:
    if item.status != "open":
        raise MelError(f"MEL item {item.id} is {item.status}; only open items can be closed or extended")


def close_item(item_id: str, actor: str) -> MelItem:
    """Rectify an open item and record the decision in the audit trail."""
    now = persist.sim_now()
    refresh_statuses(now)
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        item = _get(c, item_id)
        _require_open(item)
        item.status, item.rectified_at = "rectified", now
        _save(c, item)
    audit.record(actor, "mel_closed", item.id, "rectified", now)
    return item


def extend_item(item_id: str, actor: str, justification: str) -> MelItem:
    """Allow one fictional B +1 / C +5 day extension, capped at 2x the original period."""
    if not justification.strip():
        raise ValueError("extension requires a non-empty justification")
    now = persist.sim_now()
    refresh_statuses(now)
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        item = _get(c, item_id)
        _require_open(item)
        if item.category not in EXTENSION_DAYS:
            raise MelError(f"category {item.category} cannot be extended; only B and C allow extensions")
        if item.extensions_used:
            raise MelError("only one extension is allowed")
        expires = item.expires_at + timedelta(days=EXTENSION_DAYS[item.category])
        cap = item.deferred_at + timedelta(days=2 * _period(item.category, item.limit_days))
        if expires > cap:
            raise MelError("extension cannot exceed twice the original period")
        item.expires_at, item.extensions_used = expires, 1
        _save(c, item)
    audit.record(actor, "mel_extended", item.id, justification.strip(), now)
    return item


def open_restrictions(tail: str) -> list[str]:
    """Return restriction texts for this tail's currently open deferrals."""
    return [text for item in list_items(tail=tail, status="open") for text in item.restrictions]


def summary() -> MelSummary:
    """Summarize current open categories, deadlines within 72h, and restricted tails."""
    now = persist.sim_now()
    items = list_items(status="open")
    return MelSummary(by_category={category: sum(item.category == category for item in items) for category in MEL_INTERVAL_DAYS},
                      expiring_within_72h=sum(now < item.expires_at <= now + timedelta(hours=72) for item in items),
                      aircraft_with_open_restrictions=sorted({item.tail for item in items if item.restrictions}))


def reset() -> None:
    """Clear scenario items, retaining the per-database identifier sequence."""
    with _conn() as c:
        c.execute("DELETE FROM mel_items")
