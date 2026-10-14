"""HEALTH_MODEL=simtrained through the live path (Simulator -> twin.ingest_telemetry -> scorer), both engines.

The sim-trained models take ``age`` = cycles since the unit was last restored (train_generic.record_samples). These tests
check that what the scorer is fed at serving time equals that definition for a component's first rows, a long-running one
and one right after a restoration, and that RUL recovers after the restoration. All data are SIMULATED."""
import numpy as np
import pytest

from app import settings
from app.ml import health
from app.ml import train_generic as tg
from app.sim.service import Simulator
from app.twin import state as twin

CID = "SIM-001/hydraulics"
OTHER = "SIM-002/tires"


@pytest.fixture(scope="module")
def bundles():
    return tg.train_all(tg.tiny_config())[0]


@pytest.fixture(params=["v1", "v2"])
def live(request, bundles, monkeypatch):
    monkeypatch.setattr(settings, "SIM_ENGINE", request.param)
    monkeypatch.setenv("HEALTH_MODEL", "simtrained")
    monkeypatch.setattr(tg, "load", lambda kind, directory=None: bundles.get(kind))
    ages: dict[str, list[float]] = {}
    real = tg.with_age

    def spy(F, age):
        ages.setdefault(_current[0], []).append(float(np.asarray(age).ravel()[-1]))
        return real(F, age)

    _current = [""]
    real_score = health._score_generic_sim

    def tagged(kind, tail, component_id, window):
        _current[0] = component_id
        return real_score(kind, tail, component_id, window)

    monkeypatch.setattr(tg, "with_age", spy)
    monkeypatch.setattr(health, "_score_generic_sim", tagged)
    twin.reset()
    sim = Simulator(":memory:", ingest=twin.ingest_telemetry)
    yield sim, ages
    twin.reset()


def _run(sim, hours):
    left = hours
    while left > 0:
        step = min(left, 120)
        sim.step(step)
        left -= step


def _comp(cid):
    return next(c for c in twin.snapshot().components if c.component_id == cid)


def test_age_is_cycles_since_start_then_restoration(live):
    sim, ages = live
    _run(sim, 60)
    first = ages[CID]
    assert first[0] == 1.0 and first == [float(i) for i in range(1, len(first) + 1)]   # fresh/aged unit: observed cycles
    assert len(first) >= 8
    assert _comp(CID).rul_cycles is not None
    n = len(first)
    # a work order completes: twin reset first (workorders.complete_due), then the source restarts the series
    twin.apply_maintenance("SIM-001", CID, sim.state.now)
    sim.source.restore(CID)
    _run(sim, 60)
    after = ages[CID][n:]
    assert after and after == [float(i) for i in range(1, len(after) + 1)]            # cycles since restoration
    assert _comp(CID).cycles == len(after)       # the counter restarted too


def test_restoration_without_twin_reset_still_gives_age_since_restoration(live):
    """The source restarted the series but the twin was not told (cycle goes backwards): never a stale/negative age."""
    sim, ages = live
    _run(sim, 60)
    n = len(ages[OTHER])
    assert n >= 8
    sim.source.restore(OTHER)
    _run(sim, 60)
    after = ages[OTHER][n:]
    assert after and after == [float(i) for i in range(1, len(after) + 1)]


def test_rul_does_not_fall_and_health_recovers_after_restoration(live):
    sim, _ = live
    _run(sim, 60)
    cands = [c for c in twin.snapshot().components if c.kind in ("hydraulics", "tires") and c.rul_cycles is not None]
    worn = min(cands, key=lambda c: (c.rul_cycles.p50, c.health))      # the most worn scored unit of the fleet
    twin.apply_maintenance(worn.tail, worn.component_id, sim.state.now)
    sim.source.restore(worn.component_id)
    _run(sim, 60)
    fresh = _comp(worn.component_id)
    assert fresh.rul_cycles is not None and 0 < fresh.cycles <= len(twin._comps[worn.component_id].window)
    assert fresh.rul_cycles.p50 >= worn.rul_cycles.p50
    if worn.rul_cycles.p50 < tg.CAP:
        assert fresh.rul_cycles.p50 > worn.rul_cycles.p50          # a degraded unit gets visibly more life back
    assert fresh.health >= worn.health - 1e-9


def test_live_label_and_fallbacks(live):
    sim, _ = live
    _run(sim, 60)
    score = twin.snapshot()  # components are scored by the twin; read the scorer's own output for the label
    comp = next(c for c in score.components if c.component_id == CID)
    rows = list(twin._comps[CID].window)
    s = health.score_generic("hydraulics", "SIM-001", CID, rows)
    assert s.model_version == health.SIMTRAINED_VERSION
    assert comp.rul_cycles is not None
    # short history: scored by the model, flagged as degraded
    short = health.score_generic("hydraulics", "SIM-001", CID, rows[:5])
    assert short.model_version == health.SIMTRAINED_VERSION
    assert short.data_quality.status == "degraded" and any("rows" in r for r in short.data_quality.reasons)
    # a channel missing from the live rows: rule model, with the reason
    gone = tg.load("hydraulics")["channels"][0]
    stripped = [r.model_copy(update={"values": {k: v for k, v in r.values.items() if k != gone}}) for r in rows]
    f = health.score_generic("hydraulics", "SIM-001", CID, stripped)
    assert f.model_version == health.GENERIC_VERSION
    assert any("sim-trained" in r and gone in r for r in f.data_quality.reasons)
