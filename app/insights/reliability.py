"""GET /insights/reliability: two-parameter Weibull fit per component kind, right-censored MLE.

Basis (SIMULATED): completed lifetimes are synthesised from the simulator's life model (``wear_profile`` for the
generic kinds; for engines ``ENGINE_SLOT_LIFE`` with the same 0.8..1.25 scatter) for the previous install of each
slot; every component currently in service is right-censored at its cycles since restoration. No work-order
removal records carry cycle counts, so none are used.
"""
from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from typing import Sequence

import numpy as np

from app import settings
from app.insights import source
from app.ml.sensors import GENERIC, kind_from_component_id
from app.sim.telemetry import wear_profile

BASIS = "fit to simulated life model + fleet counters"
PRIOR_SEED_SALT = 7919
# The telemetry life model for engines (1500 flights) is a C-MAPSS run length; fleet counters run to 8200 flights
# since overhaul (app/sim/fleet.py), so the slot lifetime uses the counter scale to stay comparable with censoring.
ENGINE_SLOT_LIFE = 8200.0


def fit_weibull(times: Sequence[float], failed: Sequence[bool]) -> tuple[float, float]:
    """Maximum-likelihood (beta, eta) for right-censored data. ``failed[i]`` False means censored at times[i].

    Raises ValueError with fewer than 2 failures or non-positive times.
    """
    t = np.asarray(times, dtype="float64")
    f = np.asarray(failed, dtype=bool)
    if len(t) != len(f) or (t <= 0).any():
        raise ValueError("times must be positive and match the failure flags")
    r = int(f.sum())
    if r < 2 or float(np.ptp(t[f])) == 0.0:
        raise ValueError("need at least two distinct failure times")
    lt = np.log(t)
    mean_fail = float(lt[f].mean())
    scale = float(t.max())
    ts = t / scale
    lts = np.log(ts)

    def g(b: float) -> float:   # profile-likelihood score; increasing in b
        w = ts ** b
        return float((w * lts).sum() / w.sum() - 1.0 / b - (mean_fail - math.log(scale)))

    lo, hi = 0.05, 50.0
    if g(lo) > 0 or g(hi) < 0:
        raise ValueError("no Weibull MLE in a plausible shape range")
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if g(mid) > 0:
            hi = mid
        else:
            lo = mid
    beta = 0.5 * (lo + hi)
    eta = scale * float(((ts ** beta).sum() / r) ** (1.0 / beta))
    return beta, eta


def _rng(*parts) -> random.Random:
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def prior_life(seed: int, component_id: str) -> float:
    """Synthetic completed lifetime (flights) of the previous install of this slot."""
    kind = kind_from_component_id(component_id)
    if kind == "engine":
        return ENGINE_SLOT_LIFE * _rng(seed, component_id, "prior-life").uniform(0.8, 1.25)
    return float(wear_profile(seed ^ PRIOR_SEED_SALT, component_id, GENERIC[kind])[1])


def report() -> dict:
    seed = settings.SEED
    by_kind: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    count: dict[str, int] = defaultdict(int)
    for c in source.components():
        kind = kind_from_component_id(c.component_id)
        count[kind] += 1
        by_kind[kind].append((prior_life(seed, c.component_id), True))
        by_kind[kind].append((float(max(c.cycles, 1)), False))
    kinds = []
    for kind in sorted(by_kind):
        obs = by_kind[kind]
        try:
            beta, eta = fit_weibull([o[0] for o in obs], [o[1] for o in obs])
        except ValueError:
            continue
        kinds.append({"kind": kind, "components": count[kind], "samples": len(obs), "beta": round(beta, 3), "eta_cycles": round(eta, 1),
                      "mtbur_cycles": round(eta * math.gamma(1.0 + 1.0 / beta), 1),
                      "b10_cycles": round(eta * (-math.log(0.9)) ** (1.0 / beta), 1), "basis": BASIS})
    return {"simulated": True, "as_of": source.as_of(), "kinds": kinds}
