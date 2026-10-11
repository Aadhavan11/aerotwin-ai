"""search_kb: keyword search over the synthetic task cards (the demo's "approved documentation")."""
from __future__ import annotations

import re

from app.solver.cards import TaskCard, load_cards


def _text(c: TaskCard) -> str:
    return " ".join([c.id, c.title, c.component_kind, c.skill, c.part or "", c.note, c.ata]).lower()


def search(query: str, limit: int = 3) -> list[dict]:
    """[{doc_id, title, excerpt}] best matches first. Every hit is SYNTHETIC demo data."""
    words = [w for w in re.findall(r"[a-z0-9\-]+", query.lower()) if len(w) > 1]
    scored = []
    for c in load_cards():
        t = _text(c)
        score = sum(t.count(w) for w in words) + sum(3 for w in words if w in c.title.lower()) + (5 if c.id.lower() in query.lower() else 0)
        if score:
            scored.append((-score, c.id, c))
    out = []
    for _, _, c in sorted(scored)[:limit]:
        out.append({"doc_id": c.id, "title": c.title,
                    "excerpt": f"{c.note} {c.duration_h:g} h, skill {c.skill}" + (f", part {c.part}" if c.part else "") + " (SYNTHETIC task card)"})
    return out


def card_for_title(title: str) -> TaskCard | None:
    return next((c for c in load_cards() if c.title == title), None)
