"""Changes the copilot proposes to maintenance requests: edit one, or create a new one. It never applies them.

A proposal is the change, checked with the same rules a person's edit goes through (deadline, ground time, technician shift), plus the exact request the browser
sends if the person allows it. The person sees it with Allow and Deny; only Allow sends it, as that person, and the audit trail records them.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.contracts.models import EvidenceRef, WorkOrder
from app.copilot import workorders

PARTS = ("BORESCOPE-KIT", "BRAKE-ASSY", "HYD-PUMP", "AVIONICS-LRU")
_FIELDS = ("title", "work", "part", "part_qty", "order_part", "start", "end")


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _show(t: Any) -> str:
    if isinstance(t, datetime):
        return t.astimezone(timezone.utc).strftime("%b %d %H:%MZ").replace(" 0", " ")
    return "none" if t in (None, "") else str(t)


def propose_edit(work_order_id: str, **fields: Any) -> dict:
    """A proposed edit of a pending request. ``problems`` is empty when it would be accepted."""
    wo = workorders.get_order(work_order_id)
    if wo is None:
        return {"ok": False, "problems": [f"There is no work order {work_order_id}."]}
    if wo.status != "pending_approval":
        return {"ok": False, "problems": [f"{work_order_id} is already {wo.status}; only a request waiting for a decision can be changed."]}
    changes, body = [], {}
    for key in _FIELDS:
        val = fields.get(key)
        if val in (None, ""):
            continue
        cur = getattr(wo, key, None)
        if key in ("start", "end"):
            val = datetime.fromisoformat(str(val).replace("Z", "+00:00")) if not isinstance(val, datetime) else val
            if val.astimezone(timezone.utc) == cur.astimezone(timezone.utc):
                continue
            body[key] = _iso(val)
        else:
            if val == cur:
                continue
            body[key] = val
        changes.append({"field": {"title": "name", "work": "work to be done", "part": "part", "part_qty": "quantity", "order_part": "needs to be ordered", "start": "starts", "end": "ends"}[key],
                        "from": _show(cur) if key not in ("order_part",) else ("yes" if cur else "no"),
                        "to": _show(val) if key not in ("order_part",) else ("yes" if val else "no")})
    if not changes:
        return {"ok": False, "problems": [f"{work_order_id} already has those values; nothing would change."]}
    problems: list[str] = []
    if "start" in body or "end" in body:
        ns = datetime.fromisoformat(body.get("start", _iso(wo.start)).replace("Z", "+00:00"))
        ne = datetime.fromisoformat(body.get("end", _iso(wo.end)).replace("Z", "+00:00"))
        problems = workorders.check_timing(wo, ns, ne)
    return {"ok": not problems, "problems": problems, "id": f"edit:{work_order_id}:{abs(hash(str(body))) % 10**8}", "kind": "edit_request",
            "summary": f"Change {work_order_id} ({wo.title or wo.task_id}, {wo.tail})", "changes": changes,
            "request": {"method": "PATCH", "path": f"/workorders/{work_order_id}", "body": body}}


def propose_new(*, tail: str, station: str, start: datetime, end: datetime, title: str, work: str = "", why: str = "", part: Optional[str] = None,
                part_qty: int = 0, order_part: bool = False) -> dict:
    """A proposed new request. Checked against the aircraft's ground time at the station."""
    probe = WorkOrder(id="WO-new", tail=tail, task_id=f"M-{tail}-0", station=station, start=start, end=end, description="", status="pending_approval", plan_id="manual",
                      evidence=[EvidenceRef(kind="event", ref="manual", note="")], title=title)
    problems = workorders.check_timing(probe, start, end)
    body = {"tail": tail, "station": station, "start": _iso(start), "end": _iso(end), "title": title, "work": work, "why": why or "requested through the copilot",
            "part": part or "", "part_qty": part_qty, "order_part": order_part}
    changes = [{"field": "aircraft", "from": "", "to": tail}, {"field": "name", "from": "", "to": title}, {"field": "where", "from": "", "to": station},
               {"field": "when", "from": "", "to": f"{_show(start)} to {_show(end)}"}]
    if work:
        changes.append({"field": "work to be done", "from": "", "to": work})
    if part:
        changes.append({"field": "part", "from": "", "to": f"{part_qty or 1} x {part}" + (" (to be ordered)" if order_part else "")})
    if why:
        changes.append({"field": "why", "from": "", "to": why})
    return {"ok": not problems, "problems": problems, "id": f"new:{tail}:{_iso(start)}", "kind": "new_request", "summary": f"New request for {tail}: {title}", "changes": changes,
            "request": {"method": "POST", "path": "/workorders", "body": body}}


# ---------------------------------------------------------------------------------------------- scripted understanding
_WO = re.compile(r"\bWO[- ]?(\d+)\b", re.I)
_TAIL = re.compile(r"\bSIM[- ]?(\d{3})\b", re.I)
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b|\b(\d{1,2})\s*(am|pm)\b", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", re.I)
_KINDS = [
    (re.compile(r"brake", re.I), "Brake assembly replacement", "BRAKE-ASSY", "Brake inspection and replacement"),
    (re.compile(r"hydraul|pump", re.I), "Hydraulic pump replacement", "HYD-PUMP", "Hydraulic system check and pump replacement"),
    (re.compile(r"engine|borescope", re.I), "Engine borescope inspection", "BORESCOPE-KIT", "Borescope inspection of the engine"),
    (re.compile(r"avionic", re.I), "Avionics check", "AVIONICS-LRU", "Avionics check"),
]


def _clock(text: str) -> Optional[tuple[int, int]]:
    m = _CLOCK.search(text)
    if not m:
        return None
    if m.group(1):
        h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    else:
        h, mi, ap = int(m.group(4)), 0, m.group(5).lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return (h, mi) if h < 24 and mi < 60 else None


def _date_on(text: str, base: datetime) -> datetime:
    d = _DATE.search(text)
    if d:
        return base.replace(month=_MONTHS[d.group(1).lower()[:3]], day=int(d.group(2)), hour=0, minute=0, second=0, microsecond=0)
    if re.search(r"\btomorrow\b", text, re.I):
        return (base + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return base.replace(hour=0, minute=0, second=0, microsecond=0)


def _window_at(tail: str, start: Optional[datetime], hours: float):
    """A ground window of the aircraft: the one containing `start`, or the first that fits `hours` from now when no time was given."""
    from app.solver import service
    from app.solver.windows import ground_windows

    state, _ = service.get_state()
    now = state.now if state.now.tzinfo else state.now.replace(tzinfo=timezone.utc)
    wins = [w for w in ground_windows(state.legs, now, state.aircraft) if w.tail == tail]
    for w in wins:
        ws, we = w.start.astimezone(timezone.utc), w.end.astimezone(timezone.utc)
        if start is None and w.hours >= hours - 0.01:
            return w, ws
        if start is not None and ws <= start and start + timedelta(hours=hours) <= we + timedelta(hours=3):
            return w, start
    return (wins[0], start) if (wins and start is not None) else (None, None)


def parse_scripted(message: str, screen_text: str = "") -> Optional[tuple[str, list[dict]]]:
    """Understand a plain request to edit or create a maintenance request. Returns (answer, proposals) or None when the message is not one."""
    low = message.lower()
    wo_id = None
    m = _WO.search(message)
    if m:
        wo_id = f"WO-{int(m.group(1))}"
    elif re.search(r"\b(this|the open|that) (request|work order|job)\b", low) and screen_text:
        s = _WO.search(screen_text)
        wo_id = f"WO-{int(s.group(1))}" if s else None

    # ---- edit
    if wo_id and re.search(r"\b(rename|call|title|name|move|reschedule|shift|push|delay|change|set|edit|update|order|part|swap|replace)\b", low):
        cur = workorders.get_order(wo_id)
        fields: dict[str, Any] = {}
        t = re.search(r"\b(?:rename|call|title)\b.*?\b(?:to|as)\b\s*[\"“']?(.+?)[\"”']?\s*$", message, re.I)
        if t:
            fields["title"] = t.group(1).strip().rstrip(".")[:120]
        w = re.search(r"\bwork\b.*?(?:to|is|:)\s*[\"“']?(.+?)[\"”']?\s*$", message, re.I) if re.search(r"\b(work to be done|work is|work:|describe|set the work)\b", low) else None
        if w:
            fields["work"] = w.group(1).strip()[:600]
        p = re.search(r"\b(?:part|replace(?:ment)? item|swap)\b.*?\b(?:to|with|for|as)\b\s+([A-Za-z]{3,}-[A-Za-z]{2,})", message, re.I)
        if p:
            fields["part"] = p.group(1).upper()
        if re.search(r"\b(don'?t|do not|no need to|not) (need to )?(order|ship)|\bno order\b", low):
            fields["order_part"] = False
        elif re.search(r"\b(order|ship in)\b.*\b(part|it|pump|assembly|kit)\b|\bmark\b.*\b(to be )?ordered\b|\bneeds? (to be )?ordered\b", low):
            fields["order_part"] = True
        if cur is not None and re.search(r"\b(move|reschedule|shift|push|delay|change .*time|time to|start|earlier|later)\b", low):
            if re.search(r"\b(later|delay|push|earlier|back)\b", low) and (h := re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hours?)\b", low)):
                sign = -1 if re.search(r"\bearlier\b", low) else 1
                delta = timedelta(hours=sign * float(h.group(1)))
                fields["start"], fields["end"] = cur.start + delta, cur.end + delta
            elif (c := _clock(message)):
                day = _date_on(message, cur.start.astimezone(timezone.utc)) if (_DATE.search(message) or "tomorrow" in low) else cur.start.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                ns = day.replace(hour=c[0], minute=c[1])
                fields["start"], fields["end"] = ns, ns + (cur.end - cur.start)
        if fields:
            prop = propose_edit(wo_id, **fields)
            if prop.get("kind") is None:
                return ("; ".join(prop["problems"]), [])
            lead = f"I can make that change to {wo_id}. Nothing changes until you allow it." if prop["ok"] else f"I can't make that change to {wo_id}: " + " ".join(prop["problems"])
            return (lead, [prop])

    # ---- new request
    tail_m = _TAIL.search(message)
    if tail_m and re.search(r"\b(create|add|make|open|raise|new|log|submit|schedule|request|book)\b", low) and re.search(r"\b(request|work order|inspection|check|replace|replacement|maintenance|repair|service)\b", low):
        tail = f"SIM-{tail_m.group(1)}"
        title, part, work = "Maintenance check", None, ""
        for rx, t, prt, wk in _KINDS:
            if rx.search(low):
                title, work = t if re.search(r"replac", low) else t.replace("replacement", "inspection"), wk
                part = prt if re.search(r"replac|order|part", low) or prt == "BORESCOPE-KIT" else None
                break
        hours = 2.0
        d = re.search(r"\bfor (\d+(?:\.\d+)?)\s*(?:h|hours?)\b", low)
        if d:
            hours = max(0.5, min(12.0, float(d.group(1))))
        from app.solver import service

        now = service.get_state()[0].now
        now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        c = _clock(message)
        start = None
        if c:
            day = _date_on(message, now.astimezone(timezone.utc))
            start = day.replace(hour=c[0], minute=c[1])
            if start < now and not (_DATE.search(message) or "tomorrow" in low):
                start += timedelta(days=1)
        win, begin = _window_at(tail, start, hours)
        if win is None:
            return (f"I couldn't find a time when {tail} is on the ground for {hours:g} hours. Tell me a time or open New request and pick one from the calendar.", [])
        why = ""
        r = re.search(r"\b(?:because|since|as|due to|reason[: ]+)\s*(.+)$", message, re.I)
        if r:
            why = r.group(1).strip().rstrip(".")[:300]
        prop = propose_new(tail=tail, station=win.station, start=begin, end=begin + timedelta(hours=hours), title=title, work=work, why=why, part=part, part_qty=1 if part else 0,
                           order_part=bool(re.search(r"\border\b", low)))
        lead = (f"I can create that request for {tail}. It will wait for approval like any other, and nothing is created until you allow it." if prop["ok"]
                else "I can't create that request as asked: " + " ".join(prop["problems"]))
        return (lead, [prop])
    return None
