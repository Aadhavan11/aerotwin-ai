"""SQLite helpers (plain sqlite3 + pydantic JSON columns). Tables are defined in CONTRACTS.md section 3.

Every call opens its own short-lived connection, so it is safe to use from request threads and background threads.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional, Type, TypeVar

from pydantic import BaseModel

from . import settings

M = TypeVar("M", bound=BaseModel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS aircraft(tail TEXT PRIMARY KEY, type TEXT, home TEXT, location TEXT, status TEXT, cycles INTEGER, hours REAL, json TEXT);
CREATE TABLE IF NOT EXISTS components(tail TEXT, component_id TEXT, kind TEXT, state_json TEXT, PRIMARY KEY(tail, component_id));
CREATE TABLE IF NOT EXISTS telemetry(id TEXT PRIMARY KEY, tail TEXT, component_id TEXT, cycle INTEGER, ts TEXT, values_json TEXT);
CREATE INDEX IF NOT EXISTS ix_telemetry ON telemetry(tail, component_id, cycle);
CREATE TABLE IF NOT EXISTS health_history(tail TEXT, component_id TEXT, cycle INTEGER, ts TEXT, health REAL, anomaly REAL, rul_p50 REAL);
CREATE TABLE IF NOT EXISTS legs(id TEXT PRIMARY KEY, tail TEXT, dep TEXT, arr TEXT, sched_dep TEXT, sched_arr TEXT);
CREATE INDEX IF NOT EXISTS ix_legs_tail ON legs(tail, sched_dep);
CREATE TABLE IF NOT EXISTS stations(iata TEXT PRIMARY KEY, json TEXT);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, plan_id TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, mode TEXT, feasible INTEGER, created TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, type TEXT, at TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY, status TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS work_orders(id TEXT PRIMARY KEY, status TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, ts TEXT, actor TEXT, action TEXT, ref TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""
TABLES = ["aircraft", "components", "telemetry", "health_history", "legs", "stations", "tasks", "plans", "events", "agent_runs", "work_orders", "audit", "kv"]


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(settings.DB_PATH, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db(drop: bool = False) -> None:
    with conn() as c:
        if drop:
            for t in TABLES:
                c.execute(f"DROP TABLE IF EXISTS {t}")
        c.executescript(SCHEMA)


def is_seeded() -> bool:
    try:
        with conn() as c:
            return c.execute("SELECT COUNT(*) FROM aircraft").fetchone()[0] > 0
    except sqlite3.Error:
        return False


# ---- generic JSON-table helpers -------------------------------------------------
def put(table: str, key_col: str, key: str, model: BaseModel, **extra) -> None:
    cols = [key_col, "json", *extra.keys()]
    vals = [key, model.model_dump_json(), *extra.values()]
    with conn() as c:
        c.execute(f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)


def get(table: str, key_col: str, key: str, model: Type[M]) -> Optional[M]:
    with conn() as c:
        r = c.execute(f"SELECT json FROM {table} WHERE {key_col}=?", (key,)).fetchone()
    return model.model_validate_json(r["json"]) if r else None


def all_(table: str, model: Type[M], order_by: str = "rowid") -> list[M]:
    with conn() as c:
        rows = c.execute(f"SELECT json FROM {table} ORDER BY {order_by}").fetchall()
    return [model.model_validate_json(r["json"]) for r in rows]


# ---- key/value (sim clock, current plan id, scenario name) -----------------------
def kv_get(key: str, default: Optional[str] = None) -> Optional[str]:
    with conn() as c:
        r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def kv_set(key: str, value: str) -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (key, value))


def sim_now() -> datetime:
    """The only clock modules may use. Never call datetime.now() in app code."""
    return datetime.fromisoformat(kv_get("sim_now", settings.SIM_START).replace("Z", "+00:00"))


def set_sim_now(t: datetime) -> None:
    kv_set("sim_now", t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))


# ---- audit -------------------------------------------------------------------------
def audit(actor: str, action: str, ref: str, detail: str = "") -> None:
    with conn() as c:
        c.execute("INSERT INTO audit(id,ts,actor,action,ref,detail) VALUES(?,?,?,?,?,?)",
                  (f"A-{uuid.uuid4().hex[:10]}", sim_now().isoformat().replace("+00:00", "Z"), actor, action, ref, detail))
