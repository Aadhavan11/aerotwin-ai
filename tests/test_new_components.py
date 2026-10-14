"""The four added SIMULATED kinds preserve existing fleet and golden facts."""
import hashlib
import json
from datetime import datetime, timezone

import pytest

from app.contracts.models import HealthScore, TelemetryRow
from app.ml.health import score_component
from app.ml.sensors import GENERIC, TELEMETRY_KEYS, kind_from_component_id
from app.sim.clock import START
from app.sim.fleet import NEW_KINDS, PARTS, generate_fleet
from app.sim.telemetry import TelemetrySource, wear_profile
from app.solver.cards import rul_card_for
from app.solver.golden import golden_state
from app.solver.tasks import derive_tasks


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_new_kind_emits_telemetry_keys(kind: str) -> None:
    cid = f"SIM-001/{kind}"
    source = TelemetrySource(1234)
    assert kind_from_component_id(cid) == kind
    rows = source.advance("SIM-001", cid, 3)
    assert all(set(row.values) == set(TELEMETRY_KEYS[kind]) for row in rows)


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_wear_is_monotone_without_maintenance(kind: str) -> None:
    cid = f"SIM-001/{kind}"
    source = TelemetrySource(1234)
    previous = source._state(cid)["age"]
    for leg in generate_fleet().legs:
        if leg.tail != "SIM-001":
            continue
        source.advance_leg(leg, [cid])
        current = source.damage[cid]["age"]
        assert current >= previous
        previous = current


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_new_kind_is_deterministic(kind: str) -> None:
    cid = f"SIM-001/{kind}"
    a, b = TelemetrySource(99), TelemetrySource(99)
    assert a.advance("SIM-001", cid, 30) == b.advance("SIM-001", cid, 30)
    assert a.damage == b.damage
    assert a.modes == b.modes


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_new_kind_has_generic_health_score(kind: str) -> None:
    cid = f"SIM-001/{kind}"
    rows = TelemetrySource(1234).advance("SIM-001", cid, 30)
    score = score_component(None, "SIM-001", cid, rows)
    assert isinstance(score, HealthScore)
    assert score.component_id == cid
    assert score.top_signals == [GENERIC[kind].wear_signal]
    assert score.data_quality.status != "bad"
    assert score.rul_cycles is not None


@pytest.mark.parametrize("kind", NEW_KINDS)
@pytest.mark.parametrize("fraction", [0.0, 0.5, 1.0])
def test_new_generic_scores_follow_healthy_to_limit_direction(kind: str, fraction: float) -> None:
    spec = GENERIC[kind]
    value = spec.healthy + fraction * (spec.limit - spec.healthy)
    cid = f"SIM-001/{kind}"
    row = TelemetryRow(tail="SIM-001", component_id=cid, cycle=1, ts=START,
                       values={spec.wear_signal: value})
    score = score_component(kind, "SIM-001", cid, [row])
    assert score.health == pytest.approx(1.0 - fraction)
    assert score.rul_cycles is not None
    assert score.rul_cycles.p50 == min((1.0 - fraction) * spec.life_cycles, 500.0)


def test_engine_damage_increases_ecs_wear_on_shared_leg() -> None:
    healthy, faulted = TelemetrySource(1234), TelemetrySource(1234)
    cid = "SIM-001/ecs"
    faulted.inject("SIM-001/engine_1", "anomaly")
    legs = [leg for leg in generate_fleet().legs if leg.tail == "SIM-001"][:6]
    for leg in legs:
        healthy.advance_leg(leg, ["SIM-001/engine_1", cid])
        faulted.advance_leg(leg, ["SIM-001/engine_1", cid])
    assert faulted.damage[cid]["wear"] > healthy.damage[cid]["wear"]


def test_new_models_do_not_shift_existing_telemetry_rng() -> None:
    state = generate_fleet()
    components = [c.component_id for c in state.components if c.tail == "SIM-001"]
    legacy = [cid for cid in components if kind_from_component_id(cid) not in NEW_KINDS]
    original, expanded = TelemetrySource(1234), TelemetrySource(1234)
    for leg in [leg for leg in state.legs if leg.tail == "SIM-001"][:4]:
        expected = original.advance_leg(leg, legacy)
        actual = expanded.advance_leg(leg, components)
        assert [row for row in actual if row.component_id in legacy] == expected
    assert all(original.damage[cid] == expanded.damage[cid] for cid in legacy)


def test_new_initial_wear_matches_fleet_and_stock_is_available() -> None:
    state = generate_fleet(7)
    assert len(state.components) == 240
    assert state == generate_fleet(7)
    for tail in (a.tail for a in state.aircraft):
        components = [c for c in state.components if c.tail == tail]
        assert len(components) == 10
        assert {c.kind for c in components} >= set(NEW_KINDS)
    for component in state.components:
        if component.kind not in NEW_KINDS:
            continue
        start, life = wear_profile(7, component.component_id, GENERIC[component.kind])
        assert .05 <= start <= .35
        assert component.health == round(1 - start, 3)
        assert component.cycles == round(start * life)
        assert component.status == "ok" and component.rul_cycles is None
    assert all({p.part for p in station.parts} == set(PARTS) for station in state.stations)


def test_original_fleet_rng_draws_are_unchanged() -> None:
    # Fingerprint computed from the unmodified fleet generator at HEAD.
    state = generate_fleet()
    legacy = {
        "aircraft": [(a.tail, a.cycles, a.hours) for a in state.aircraft],
        "components": [(c.component_id, c.cycles, c.health, c.last_maintenance.isoformat())
                       for c in state.components if c.kind not in NEW_KINDS],
        "legs": [(leg.id, leg.dep, leg.arr) for leg in state.legs],
        "stations": [(s.iata, [(t.id, t.available) for t in s.technicians],
                      [(p.part, p.qty) for p in s.parts if p.part in PARTS[:4]]) for s in state.stations],
    }
    digest = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
    assert digest == "27908a0568c5ffd16c0acf27a0687fdd7266c5a40132afd2a4aaabfa21e60eca"


@pytest.mark.parametrize("kind,skill,part,ata", [
    ("apu", "engine", "APU-STARTER", "ATA 49"),
    ("tires", "gear", "TIRE-ASSY", "ATA 32"),
    ("ecs", "hydraulics", "ECS-PACK-VALVE", "ATA 21"),
    ("electrical", "avionics", "BATTERY-PACK", "ATA 24"),
])
def test_new_task_cards_use_existing_skills(kind: str, skill: str, part: str, ata: str) -> None:
    card = rul_card_for(kind)
    assert card is not None
    assert (card.skill, card.part, card.ata) == (skill, part, ata)
    assert card.synthetic and "SIMULATED" in card.note


def test_sim017_golden_task_list_is_unchanged() -> None:
    # IDs/values from the original golden tests; deadline computed from HEAD's rotation.
    state = golden_state()
    tasks = [t for t in derive_tasks(state) if t.tail == "SIM-017"]
    assert [(t.id, t.component_id, t.duration_h, t.required_skill, t.required_part,
             t.part_qty, t.source, t.earliest, t.deadline) for t in tasks] == [
        ("T-SIM-017-engine_2-001", "SIM-017/engine_2", 6.0, "engine", "BORESCOPE-KIT",
         1, "rul", datetime(2026, 10, 12, 12, tzinfo=timezone.utc),
         datetime(2026, 10, 17, 4, 40, tzinfo=timezone.utc)),
    ]
    assert len(derive_tasks(state)) == 5
    engine = next(c for c in state.components if c.component_id == "SIM-017/engine_2")
    assert engine.rul_cycles is not None
    assert (engine.rul_cycles.p10, engine.rul_cycles.p50, engine.rul_cycles.p90) == (18, 22, 25)
    dfw = next(s for s in state.stations if s.iata == "DFW")
    assert next(p for p in dfw.parts if p.part == "BORESCOPE-KIT").qty == 2
