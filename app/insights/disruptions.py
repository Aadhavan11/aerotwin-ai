"""Weather / station disruptions as a pure overlay on the schedule (never mutates FleetState).

Events are stored as one JSON list in ``kv`` (shared DB) when persistence is on, in memory otherwise.
A leg whose departure from, or arrival to, the event station falls in the event window slips by
severity * duration_h. Delay rolls along a tail's rotation, absorbed by the buffer between the leg's
scheduled arrival and the next scheduled departure minus ``MIN_TURN_MIN``.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from threading import RLock

from app.copilot import persist
from app.insights import source

MIN_TURN_MIN = 35.0
KV_KEY = "insights_disruptions"
_LOCK = RLock()
_MEM: list[dict] = []


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load() -> list[dict]:
    if persist.enabled():
        with persist.conn() as c:
            row = c.execute("SELECT value FROM kv WHERE key=?", (KV_KEY,)).fetchone()
        return json.loads(row["value"]) if row else []
    return [dict(e) for e in _MEM]


def _save(events: list[dict]) -> None:
    if persist.enabled():
        with persist.conn() as c:
            c.execute("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (KV_KEY, json.dumps(events)))
    else:
        _MEM[:] = [dict(e) for e in events]


def reset() -> None:
    """Forget every disruption (scenario reset)."""
    with _LOCK:
        _MEM.clear()
        if persist.enabled():
            try:
                with persist.conn() as c:
                    c.execute("DELETE FROM kv WHERE key=?", (KV_KEY,))
            except Exception:  # noqa: BLE001
                pass


def list_events() -> list[dict]:
    with _LOCK:
        return sorted(_load(), key=lambda e: (e["start"], e["id"]))


def add_event(station: str, start: datetime, duration_h: float, severity: float) -> dict:
    with _LOCK:
        events = _load()
        n = max((int(m.group(1)) for e in events if (m := re.match(r"WX-(\d+)$", e["id"]))), default=0) + 1
        event = {"id": f"WX-{n}", "station": station, "start": _iso(start), "duration_h": float(duration_h), "severity": float(severity)}
        events.append(event)
        _save(events)
        return event


def remove_event(event_id: str) -> bool:
    with _LOCK:
        events = _load()
        kept = [e for e in events if e["id"] != event_id]
        if len(kept) == len(events):
            return False
        _save(kept)
        return True


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def propagate(events: list[dict], legs) -> list[dict]:
    """Delayed legs (``delay_min`` > 0) for ``events`` over ``legs``; deterministic, input untouched."""
    windows = [(e, _parse(e["start"]), _parse(e["start"]) + timedelta(hours=e["duration_h"]), e["severity"] * e["duration_h"] * 60.0) for e in events]
    by_tail: dict[str, list] = {}
    for lg in legs:
        by_tail.setdefault(lg.tail, []).append(lg)
    out: list[dict] = []
    for tail in sorted(by_tail):
        rotation = sorted(by_tail[tail], key=lambda lg: (lg.sched_dep, lg.id))
        carry, prev = 0.0, None   # minutes of delay that the previous leg's buffer could not absorb
        for lg in rotation:
            dep_direct = sum(slip for e, a, b, slip in windows if e["station"] == lg.dep and a <= lg.sched_dep < b)
            arr_direct = sum(slip for e, a, b, slip in windows if e["station"] == lg.arr and a <= lg.sched_arr < b)
            dep_delay = max(dep_direct, carry)
            arr_delay = dep_delay + arr_direct
            if arr_delay > 0:
                direct = dep_direct > 0 or arr_direct > 0
                hit = [e["id"] for e, a, b, _ in windows if (e["station"] == lg.dep and a <= lg.sched_dep < b) or (e["station"] == lg.arr and a <= lg.sched_arr < b)]
                cause = f"weather at {lg.dep if dep_direct > 0 else lg.arr} ({', '.join(hit)})" if direct else f"rotation knock-on from {prev.id}"
                out.append({"leg_id": lg.id, "tail": tail, "dep": lg.dep, "arr": lg.arr, "sched_dep": _iso(lg.sched_dep),
                            "est_dep": _iso(lg.sched_dep + timedelta(minutes=dep_delay)), "sched_arr": _iso(lg.sched_arr),
                            "est_arr": _iso(lg.sched_arr + timedelta(minutes=arr_delay)), "delay_min": round(arr_delay, 1), "cause": cause})
            prev = lg
            carry = 0.0
            nxt = rotation[rotation.index(lg) + 1] if rotation.index(lg) + 1 < len(rotation) else None
            if nxt is not None and arr_delay > 0:
                buffer = (nxt.sched_dep - lg.sched_arr).total_seconds() / 60.0 - MIN_TURN_MIN
                carry = max(0.0, arr_delay - max(buffer, 0.0))
    return out


def report() -> dict:
    events = list_events()
    delayed = propagate(events, source.legs()) if events else []
    tails: dict[str, dict] = {}
    for d in delayed:
        t = tails.setdefault(d["tail"], {"tail": d["tail"], "legs_affected": 0, "total_delay_min": 0.0})
        t["legs_affected"] += 1
        t["total_delay_min"] = round(t["total_delay_min"] + d["delay_min"], 1)
    return {"simulated": True, "as_of": source.as_of(), "events": events, "delayed_legs": delayed,
            "by_tail": [tails[k] for k in sorted(tails)]}
