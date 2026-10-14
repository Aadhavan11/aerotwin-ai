"""Model modules register themselves on import; later phases add models/{brakes,tires,...}.py with no other edits."""

from __future__ import annotations

import importlib
import pkgutil


def load_all() -> None:
    for info in pkgutil.iter_modules(__path__):
        try:
            importlib.import_module(f"{__name__}.{info.name}")
        except ImportError:
            continue


load_all()
