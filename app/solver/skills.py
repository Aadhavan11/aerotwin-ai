"""Technician skill levels, without changing the shared models.

``Technician.skills`` and ``Task.required_skill`` stay plain strings. ``"engine"`` means level 1; ``"engine:2"`` means level 2
(a senior / certified technician). A technician satisfies a requirement if he holds that skill at the required level or higher,
so ``"engine:2"`` also covers plain ``"engine"`` tasks. Nothing is invented: a skill string without a level is level 1.
"""
from __future__ import annotations

from typing import Iterable


def parse_skill(spec: str) -> tuple[str, int]:
    name, _, level = spec.partition(":")
    try:
        return name, max(1, int(level)) if level else 1
    except ValueError:
        return name, 1


def has_skill(held: Iterable[str], required: str) -> bool:
    need_name, need_level = parse_skill(required)
    return any(n == need_name and lvl >= need_level for n, lvl in map(parse_skill, held))


def skill_label(required: str) -> str:
    name, level = parse_skill(required)
    return name if level == 1 else f"{name} (level {level})"


def with_level(skill: str, min_level: int) -> str:
    return skill if min_level <= 1 else f"{skill}:{min_level}"
