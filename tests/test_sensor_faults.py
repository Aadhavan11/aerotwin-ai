"""Sensor-fault signatures, reproducibility, and health-scoring integration."""

from __future__ import annotations

import json
import random
from copy import deepcopy
from datetime import UTC, datetime
from statistics import mean, stdev

import pytest

from app.contracts.models import TelemetryRow
from app.ml.health import score_generic
from app.sim.sensor_faults import (
    MODES,
    apply,
    empty_state,
    faults_active,
    start_fault,
    stochastic_onset,
)

CID = "SIM-001/brakes"
TS = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def test_empty_state_is_independent() -> None:
    a, b = empty_state(), empty_state()
    a["last"]["wear"] = 1.0
    assert b == {"faults": {}, "last": {}}


@pytest.mark.parametrize("mode", MODES)
def test_mode_signature_and_window(mode: str) -> None:
    rng = random.Random(123)
    state = start_fault(empty_state(), "wear", mode, 2, 4, 2.0)
    out, state = apply(CID, 1, {"wear": 10.0}, state, rng)
    assert out == {"wear": 10.0}
    for offset in range(4):
        value = 20.0 + offset
        out, state = apply(CID, 2 + offset, {"wear": value}, state, rng)
        if mode == "dropout":
            assert "wear" not in out
        elif mode == "stuck":
            assert out["wear"] == 10.0
            assert state["faults"]["wear"]["frozen"] == 10.0
        elif mode == "drift":
            assert out["wear"] == value + 2.0 * (offset + 1)
        elif mode == "spike":
            assert out["wear"] == value + (2.0 if offset == 0 else 0.0)
        else:
            assert out["wear"] != value
        assert state["last"]["wear"] == 10.0
        if mode != "stuck":
            assert "frozen" not in state["faults"]["wear"]
    out, state = apply(CID, 6, {"wear": 99.0}, state, rng)
    assert out == {"wear": 99.0}
    assert state == {"faults": {}, "last": {"wear": 99.0}}


def test_stuck_without_previous_good_value_is_constant() -> None:
    state = start_fault(empty_state(), "wear", "stuck", 1, 30, 0.0)
    rng = random.Random(1)
    for cycle in range(1, 31):
        out, state = apply(CID, cycle, {"wear": float(cycle)}, state, rng)
        assert out == {"wear": 1.0}
        assert state["faults"]["wear"]["frozen"] == 1.0
        assert state["last"] == {}


def test_noise_distribution() -> None:
    rng = random.Random(42)
    state = start_fault(empty_state(), "wear", "noise", 1, 500, 2.0)
    samples = []
    for cycle in range(1, 501):
        out, state = apply(CID, cycle, {"wear": 10.0}, state, rng)
        samples.append(out["wear"])
    assert abs(mean(samples) - 10.0) < 0.3
    assert abs(stdev(samples) - 2.0) < 0.3 * 2.0


def test_start_fault_copies_and_replaces() -> None:
    state = start_fault(empty_state(), "wear", "stuck", 1, 10, 0.0)
    _, state = apply(CID, 1, {"wear": 5.0}, state, random.Random(1))
    state = start_fault(state, "other", "dropout", 2, 4, 0.0)
    before = deepcopy(state)
    new = start_fault(state, "wear", "drift", 3, 2, 1.5)
    assert state == before
    assert new["faults"]["wear"] == {
        "mode": "drift", "start": 3, "duration": 2, "magnitude": 1.5
    }
    new["faults"]["other"]["duration"] = 100
    new["last"]["wear"] = 99.0
    assert state == before


@pytest.mark.parametrize("mode,duration", [("unknown", 1), ("noise", 0), ("noise", -1)])
def test_invalid_fault(mode: str, duration: int) -> None:
    with pytest.raises(ValueError):
        start_fault(empty_state(), "wear", mode, 1, duration, 1.0)


def test_fault_listing_and_expiration_of_absent_keys() -> None:
    state = start_fault(empty_state(), "z", "noise", 1, 2, 1.0)
    state = start_fault(state, "a", "stuck", 5, 2, 1.0)
    assert faults_active(state) == [{"key": "a", "mode": "stuck"}, {"key": "z", "mode": "noise"}]
    _, new = apply(CID, 3, {}, state, random.Random(1))
    assert faults_active(new) == [{"key": "a", "mode": "stuck"}]
    assert "z" in state["faults"]


@pytest.mark.parametrize("mode", MODES)
def test_apply_does_not_mutate_inputs(mode: str) -> None:
    state = start_fault(empty_state(), "wear", mode, 1, 3, 2.0)
    values = {"wear": 10.0, "good": 50.0}
    before = deepcopy(state)
    out, new = apply(CID, 1, values, state, random.Random(1))
    assert state == before
    assert values == {"wear": 10.0, "good": 50.0}
    assert out is not values
    new["faults"]["wear"]["duration"] = 100
    new["last"]["good"] = 99.0
    assert state == before


def test_only_active_present_noise_consumes_rng() -> None:
    rng = random.Random(123)
    state = start_fault(empty_state(), "absent", "noise", 1, 10, 2.0)
    state = start_fault(state, "wear", "noise", 5, 2, 2.0)
    before = rng.getstate()
    for cycle in (1, 4, 7):
        _, state = apply(CID, cycle, {"wear": 10.0}, state, rng)
    assert rng.getstate() == before
    state = start_fault(state, "wear", "noise", 8, 1, 2.0)
    expected = random.Random(123)
    out, _ = apply(CID, 8, {"wear": 10.0}, state, rng)
    assert out["wear"] == 10.0 + expected.gauss(0.0, 2.0)
    assert rng.getstate() == expected.getstate()


def test_determinism_json_round_trips_and_global_rng() -> None:
    state = empty_state()
    for index, mode in enumerate(MODES):
        state = start_fault(state, mode, mode, 1 + index * 3, 35, 2.0)
    a, b = deepcopy(state), deepcopy(state)
    rng_a, rng_b = random.Random(17), random.Random(17)
    global_before = random.getstate()
    for cycle in range(1, 61):
        values = {mode: float(cycle) for mode in MODES}
        if cycle in (1, 2, 15, 35, 45, 60):
            restored = json.loads(json.dumps(a))
            assert restored == a
            replay_rng = random.Random()
            replay_rng.setstate(rng_a.getstate())
            replay = apply(CID, cycle, values, restored, replay_rng)
        out_a, a = apply(CID, cycle, values, a, rng_a)
        out_b, b = apply(CID, cycle, values, b, rng_b)
        assert (out_a, a) == (out_b, b)
        assert json.loads(json.dumps(a)) == a
        if cycle in (1, 2, 15, 35, 45, 60):
            assert replay == (out_a, a)
    assert random.getstate() == global_before


def test_stochastic_onset() -> None:
    rng = random.Random(42)
    assert not any(stochastic_onset(rng, 0.0) for _ in range(100))
    assert all(stochastic_onset(rng, 1.0) for _ in range(100))
    a, b, expected = random.Random(7), random.Random(7), random.Random(7)
    sequence = [stochastic_onset(a, 0.4) for _ in range(100)]
    assert sequence == [stochastic_onset(b, 0.4) for _ in range(100)]
    assert sequence == [expected.random() < 0.4 for _ in range(100)]
    assert any(sequence) and not all(sequence)


@pytest.mark.parametrize("rate", [-0.1, 1.1, float("nan"), float("inf"), -float("inf")])
def test_invalid_onset_rate(rate: float) -> None:
    with pytest.raises(ValueError):
        stochastic_onset(random.Random(1), rate)


@pytest.mark.parametrize("mode,start,duration,status,reason", [
    (None, 0, 1, "ok", None),
    ("dropout", 30, 1, "bad", "missing"),
    ("dropout", 10, 1, "degraded", "missing"),
    ("stuck", 1, 30, "degraded", "flat"),
])
def test_brakes_health_quality(
    mode: str | None, start: int, duration: int, status: str, reason: str | None
) -> None:
    rng = random.Random(123)
    # A prior good zero freezes exactly: the scorer compares np.std to zero,
    # and repeated 0.1 can have a tiny nonzero standard deviation.
    _, state = apply(CID, 0, {"pad_wear_mm": 0.0}, empty_state(), rng)
    if mode is not None:
        state = start_fault(state, "pad_wear_mm", mode, start, duration, 0.0)
    rows = []
    for cycle in range(1, 31):
        values, state = apply(
            CID, cycle, {"pad_wear_mm": 0.1 * cycle, "disc_temp_c": 50.0}, state, rng
        )
        rows.append(TelemetryRow(
            tail="SIM-001", component_id=CID, ts=TS, cycle=cycle, values=dict(values)
        ))
    score = score_generic("brakes", "SIM-001", CID, rows)
    assert score.data_quality.status == status
    if reason is None:
        assert score.data_quality.reasons == []
    else:
        assert any(reason in text for text in score.data_quality.reasons)
    if status == "bad":
        assert score.rul_cycles is None
        assert score.health == 0.5
        assert score.top_signals == []
