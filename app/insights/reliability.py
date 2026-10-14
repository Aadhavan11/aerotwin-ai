"""GET /insights/reliability: two-parameter Weibull fit per component kind, right-censored MLE.

Two bases (both SIMULATED):

* ``report()`` (v1 callers, SIM_ENGINE=v1 fleet counters): completed lifetimes are synthesised from the v1 telemetry life
  model (``wear_profile`` for the generic kinds; for engines the legacy counter scale ``ENGINE_SLOT_LIFE`` with the same
  0.8..1.25 scatter) for the previous install of each slot; every component in service is right-censored at its cycles
  since restoration. v1 work orders carry no cycle counts, so none are used. This is NOT consistent with the v2 removal
  records and must not be shown next to them.
* ``fit_lives()`` (the reliability pack): right-censored MLE on the v2 removal / install-time records of the same dataset
  the pack's MTBUR/MTBF come from. Failures are unscheduled, non-NFF removals; planned removals and units still in service
  are suspensions censored at their cycles since last restoration (new or overhaul).
"""
from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from typing import Iterable, Sequence

import numpy as np

from app import settings
from app.insights import source
from app.ml.sensors import GENERIC, kind_from_component_id
from app.sim.constants import LEGACY_V1_ENGINE_COUNTER_CAP
from app.sim.telemetry import wear_profile

BASIS = "fit to simulated life model + fleet counters"
BASIS_V2 = ("right-censored MLE on v2 removal/install records (failures: unscheduled non-NFF removals; planned removals and "
            "in-service units censored at cycles since last restoration; lives start at simulation start, so pre-aged first lives are biased short)")
PRIOR_SEED_SALT = 7919
# v1 ONLY. The telemetry life model for engines (1500 flights) is a C-MAPSS run length; v1 fleet counters run to
# ``LEGACY_V1_ENGINE_COUNTER_CAP`` flights since overhaul (app/sim/fleet.py), so the v1 slot lifetime uses that counter scale
# to stay comparable with its censoring. It is a legacy counter scale, not the v2 engine on-wing life
# (``app.sim.constants.ENGINE_MEAN_ON_WING_FLIGHTS``).
ENGINE_SLOT_LIFE = float(LEGACY_V1_ENGINE_COUNTER_CAP)


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


def fit_lives(lives: Iterable[tuple[str, float, bool]], components: dict[str, int] | None = None,
              unit_definitions: dict[str, str] | None = None, min_failures: int = 2) -> list[dict]:
    """Per-kind right-censored Weibull MLE from ``(kind, life_cycles, failed)`` triples (failed False = suspension).

    Used by the reliability pack on v2 removal / install-time records. Kinds with fewer than ``min_failures`` distinct
    failure times (or no Weibull MLE in a plausible shape range) are omitted. ``beta`` is the shape, ``eta_cycles`` the
    scale, ``mean_life_cycles`` = eta * Gamma(1 + 1/beta); ``mtbur_cycles`` repeats it for consumers of the v1 key.
    """
    by_kind: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for kind, life, failed in lives:
        if life > 0:
            by_kind[kind].append((float(life), bool(failed)))
    out = []
    for kind in sorted(by_kind):
        obs = by_kind[kind]
        n_fail = sum(1 for _, f in obs if f)
        if n_fail < min_failures:
            continue
        try:
            beta, eta = fit_weibull([o[0] for o in obs], [o[1] for o in obs])
        except ValueError:
            continue
        mean = eta * math.gamma(1.0 + 1.0 / beta)
        out.append({"kind": kind, "components": int((components or {}).get(kind, 0)), "samples": len(obs), "failures": n_fail,
                    "censored": len(obs) - n_fail, "beta": round(beta, 3), "eta_cycles": round(eta, 1),
                    "mean_life_cycles": round(mean, 1), "mtbur_cycles": round(mean, 1),
                    "b10_cycles": round(eta * (-math.log(0.9)) ** (1.0 / beta), 1), "basis": BASIS_V2,
                    "unit_definition": (unit_definitions or {}).get(kind, "")})
    return out
