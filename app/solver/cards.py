"""Loader for the synthetic task cards (app/copilot/kb/task_cards.yaml).

The cards are authored by Pardhu and read by derive_tasks and by Shamit's copilot (search_kb).
Every card must carry ``synthetic: true``; the loader refuses to load a card that does not.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

CARDS_PATH = Path(__file__).resolve().parent.parent / "copilot" / "kb" / "task_cards.yaml"

KINDS = {"engine", "brakes", "landing_gear", "hydraulics", "avionics", "apu", "tires", "ecs", "electrical"}
SKILLS = {"engine", "gear", "hydraulics", "avionics"}


@dataclass(frozen=True)
class TaskCard:
    id: str
    title: str
    component_kind: str
    ata: str
    duration_h: float
    skill: str
    part: Optional[str]
    part_qty: int
    trigger: str                      # "rul" | "scheduled"
    interval_days: Optional[float]    # scheduled cards only
    min_level: int                    # technician skill level needed (1 = any qualified technician)
    note: str
    synthetic: bool = True


def _parse(raw: dict) -> TaskCard:
    if raw.get("synthetic") is not True:
        raise ValueError(f"task card {raw.get('id')!r} must be marked synthetic: true")
    kind, skill, trigger = raw["component_kind"], raw["skill"], raw["trigger"]
    if kind not in KINDS:
        raise ValueError(f"task card {raw['id']}: unknown component_kind {kind!r}")
    if skill not in SKILLS:
        raise ValueError(f"task card {raw['id']}: unknown skill {skill!r}")
    if trigger not in ("rul", "scheduled"):
        raise ValueError(f"task card {raw['id']}: trigger must be rul or scheduled, got {trigger!r}")
    interval = raw.get("interval_days")
    if trigger == "scheduled" and not interval:
        raise ValueError(f"task card {raw['id']}: scheduled cards need interval_days")
    part = raw.get("part")
    return TaskCard(
        id=raw["id"],
        title=raw["title"],
        component_kind=kind,
        ata=str(raw.get("ata", "")),
        duration_h=float(raw["duration_h"]),
        skill=skill,
        part=part,
        part_qty=int(raw.get("part_qty") or (1 if part else 0)),
        trigger=trigger,
        interval_days=float(interval) if interval else None,
        min_level=int(raw.get("min_level", 1)),
        note=raw.get("note", ""),
    )


@lru_cache(maxsize=4)
def _load(path: str) -> tuple[TaskCard, ...]:
    data = yaml.safe_load(Path(path).read_text())
    cards = tuple(_parse(c) for c in data["cards"])
    ids = [c.id for c in cards]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task card ids")
    return cards


def load_cards(path: Optional[Path] = None) -> list[TaskCard]:
    return list(_load(str(path or CARDS_PATH)))


def rul_card_for(kind: str, cards: Optional[list[TaskCard]] = None) -> Optional[TaskCard]:
    """The card used when a component of this kind has a low RUL (first rul card in file order)."""
    for c in cards or load_cards():
        if c.trigger == "rul" and c.component_kind == kind:
            return c
    return None


def scheduled_cards_for(kind: str, cards: Optional[list[TaskCard]] = None) -> list[TaskCard]:
    return [c for c in (cards or load_cards()) if c.trigger == "scheduled" and c.component_kind == kind]
