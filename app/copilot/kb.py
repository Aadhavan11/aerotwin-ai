"""Synthetic knowledge base: maintenance task cards (``app/copilot/kb/*.yaml``).

SYNTHETIC DEMO DATA. The cards are made up for the prototype (see the header of
the YAML file). The copilot cites them as ``kb:<id>`` and must not invent part
numbers, limits or intervals that are not in a card.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

KB_DIR = Path(__file__).resolve().parent / "kb"
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "for", "on", "in", "to", "and", "or", "is", "are", "what", "which", "how", "do", "does"}


@lru_cache(maxsize=1)
def load_cards() -> tuple[dict, ...]:
    cards: list[dict] = []
    for path in sorted(KB_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for c in doc.get("cards", []):
            cards.append({**c, "source_file": path.name})
    return tuple(cards)


def reload() -> None:
    load_cards.cache_clear()


def get_card(card_id: str) -> Optional[dict]:
    return next((c for c in load_cards() if c.get("id") == card_id), None)


def card_for_title(title: str) -> Optional[dict]:
    return next((c for c in load_cards() if c.get("title") == title), None)


def default_card_for(component_kind: str) -> Optional[dict]:
    """The card the scheduler uses for a low-RUL task on this kind of component."""
    for c in load_cards():
        if c.get("component_kind") == component_kind and c.get("trigger") == "rul":
            return c
    return None


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def search_kb(query: str, limit: int = 3) -> list[dict]:
    """Keyword search. Returns ``[{doc_id, title, excerpt, score}]``, best first; empty if nothing matches."""
    q = set(_tokens(query))
    if not q:
        return []
    scored = []
    for c in load_cards():
        title = set(_tokens(c.get("title", "")))
        body = set(_tokens(" ".join(str(c.get(k, "")) for k in ("note", "component_kind", "ata", "skill", "part"))))
        score = 3 * len(q & title) + len(q & body)
        if score:
            scored.append((score, c))
    scored.sort(key=lambda sc: (-sc[0], sc[1]["id"]))
    return [
        {"doc_id": c["id"], "title": c.get("title", ""), "excerpt": c.get("note", ""), "score": s,
         "duration_h": c.get("duration_h"), "skill": c.get("skill"), "part": c.get("part"), "synthetic": True}
        for s, c in scored[:limit]
    ]
