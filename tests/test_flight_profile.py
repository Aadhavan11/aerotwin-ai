"""Flight profiles are reproducible and obey their operational constraints."""

import hashlib
import random
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta
from statistics import mean

import pytest

from app.contracts.models import Leg
from app.sim.profile import (
    FlightProfile,
    brake_energy_mj,
    profile_for,
    profile_for_cycle,
)

PHASE_NAMES = (
    "taxi_out", "takeoff", "climb", "cruise", "descent", "landing", "taxi_in"
)


def _leg(dep: str = "ATL", arr: str = "DFW", minutes: float = 120.0) -> Leg:
    departure = datetime(2026, 10, 13, 12, 0)
    return Leg(
        id="L-SIM-017-001",
        tail="SIM-017",
        dep=dep,
        arr=arr,
        sched_dep=departure,
        sched_arr=departure + timedelta(minutes=minutes),
    )


def test_determinism() -> None:
    leg = _leg()
    assert profile_for(42, leg) == profile_for(42, leg)
    assert profile_for(42, leg) != profile_for(43, leg)
    assert profile_for_cycle(42, "SIM-017/brakes", 1) == profile_for_cycle(
        42, "SIM-017/brakes", 1
    )
    assert profile_for_cycle(42, "SIM-017/brakes", 1) != profile_for_cycle(
        42, "SIM-017/brakes", 2
    )
    assert profile_for_cycle(42, "SIM-017/brakes", 1) != profile_for_cycle(
        43, "SIM-017/brakes", 1
    )
    assert profile_for_cycle(42, "SIM-017/brakes", 1) != profile_for_cycle(
        42, "SIM-018/brakes", 1
    )


def test_leg_seed_key() -> None:
    leg = _leg()
    key = (
        f"profile|42|{leg.id}|{leg.tail}|{leg.dep}|{leg.arr}|"
        f"{leg.sched_dep.isoformat()}|{leg.sched_arr.isoformat()}"
    )
    rng = random.Random(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big"))
    assert profile_for(42, leg).phases[0][1] == round(11 + rng.uniform(-2, 2), 2)


def test_ranges_and_rounding() -> None:
    for seed in range(200):
        for profile in (profile_for(seed, _leg()), profile_for_cycle(seed, "brakes", 1)):
            assert isinstance(profile, FlightProfile)
            assert 0.55 <= profile.payload_frac <= 0.95
            assert 0.0 <= profile.takeoff_derate <= 0.15
            assert 240 <= profile.cruise_fl <= 390
            assert profile.cruise_fl % 10 == 0
            assert 0.60 <= profile.landing_weight_frac <= 0.98
            assert 1.5 <= profile.sink_rate_fps <= 5.0
            assert isinstance(profile.wet_runway, bool)
            assert tuple(name for name, _ in profile.phases) == PHASE_NAMES
            assert all(minutes > 0 for _, minutes in profile.phases)
            assert sum(minutes for _, minutes in profile.phases) == pytest.approx(
                120.0, abs=0.02
            )
            assert all(minutes == round(minutes, 2) for _, minutes in profile.phases)
            for field in fields(profile):
                value = getattr(profile, field.name)
                if isinstance(value, float):
                    assert value == round(value, 3)


@pytest.mark.parametrize("minutes", [30.0, 30.001, 120.0, 120.123456])
def test_phase_totals_and_short_leg_shrinking(minutes: float) -> None:
    for seed in range(200):
        profile = profile_for(seed, _leg("JFK", "SFO", minutes))
        assert sum(value for _, value in profile.phases) == pytest.approx(minutes, abs=0.02)
        assert all(value > 0 for _, value in profile.phases)
        assert dict(profile.phases)["landing"] == 1.0
        if minutes < 31:
            assert dict(profile.phases)["cruise"] == 5.0


def test_arrival_density_altitude_and_climate() -> None:
    profiles = {
        arr: [profile_for(seed, _leg("JFK", arr)) for seed in range(50)]
        for arr in ("DEN", "MIA", "ATL")
    }
    climates = {
        "DEN": (5434.0, 13.0, 12000.0, 6400.0),
        "MIA": (8.0, 29.0, 8600.0, 1700.0),
        "ATL": (1026.0, 19.0, 9000.0, 1750.0),
    }
    for arr, (elevation, oat, runway, expected_mean) in climates.items():
        for profile in profiles[arr]:
            assert profile.field_elev_arr_ft == elevation
            assert profile.field_elev_dep_ft == 13.0
            assert 12.0 <= profile.oat_dep_c <= 16.0
            assert oat - 2 <= profile.oat_arr_c <= oat + 2
            assert profile.runway_len_ft == runway
            isa = 15.0 - 1.98 * elevation / 1000.0
            assert profile.density_alt_arr_ft == pytest.approx(
                elevation + 120 * (profile.oat_arr_c - isa), abs=0.001
            )
        assert mean(p.density_alt_arr_ft for p in profiles[arr]) == pytest.approx(
            expected_mean, abs=120.0
        )
    den_mean = mean(p.density_alt_arr_ft for p in profiles["DEN"])
    for arr in ("MIA", "ATL"):
        assert den_mean > mean(p.density_alt_arr_ft for p in profiles[arr])
        assert all(
            den.density_alt_arr_ft > other.density_alt_arr_ft
            for den, other in zip(profiles["DEN"], profiles[arr])
        )


def test_brake_energy() -> None:
    assert brake_energy_mj(0.7, 9000, False) < brake_energy_mj(0.9, 9000, False)
    assert brake_energy_mj(0.8, 9000, True) > brake_energy_mj(0.8, 9000, False)
    assert brake_energy_mj(0.8, 12000, False) < brake_energy_mj(0.8, 9000, False)
    assert brake_energy_mj(0.8, 8000, True) == pytest.approx(22.0 * 0.8 * 1.35)
    for seed in range(200):
        profile = profile_for(seed, _leg())
        assert profile.brake_energy_mj == round(brake_energy_mj(
            profile.landing_weight_frac, profile.runway_len_ft, profile.wet_runway
        ), 3)


@pytest.mark.parametrize("runway", [0.0, -1.0])
def test_invalid_runway(runway: float) -> None:
    with pytest.raises(ValueError):
        brake_energy_mj(0.8, runway, False)


@pytest.mark.parametrize("dep,arr", [("ATL", "ATL"), ("XXX", "ATL"), ("ATL", "XXX")])
def test_invalid_stations(dep: str, arr: str) -> None:
    with pytest.raises(ValueError):
        profile_for(42, _leg(dep, arr))


@pytest.mark.parametrize("minutes", [-1.0, 0.0, 29.999])
def test_invalid_duration(minutes: float) -> None:
    with pytest.raises(ValueError):
        profile_for(42, _leg(minutes=minutes))


def test_frozen_profile() -> None:
    with pytest.raises(FrozenInstanceError):
        profile_for(42, _leg()).payload_frac = 0.8
