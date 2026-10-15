"""Provenance and independent coverage checks against the live simulation objects."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from app.sim import constants
from app.sim.v2 import coupling, drivers, faults, world
from app.sim.v2.models import electrical, engine, hydraulics
from app.sim.v2.params_registry import ROLES, bounds, coverage_gaps, get, registry, render_markdown

_ROOT = Path(__file__).resolve().parents[1]


def test_no_coverage_gaps() -> None:
    assert coverage_gaps() == ([], [])


def test_calibrated_differ_from_prior() -> None:
    fit = json.loads((_ROOT / "app/sim/v2/calibration/electrical_fit.json").read_text(encoding="utf-8"))
    calibrated = [param for param in registry() if param.source_type == "calibrated"]
    assert {param.id for param in calibrated} == {
        "v2.models.electrical.PARAMS[resistance_fade]",
        "v2.models.electrical.PARAMS[capacity_noise]",
        "v2.models.electrical.PARAMS[resistance_noise]",
    }
    for param in calibrated:
        key = param.id.split("[")[1].rstrip("]")
        assert electrical.PRIOR_PARAMS[key] != param.value
        assert fit["decisions"][key]["prior"] == electrical.PRIOR_PARAMS[key]
        assert fit["overrides"][key] == param.value


def test_assumed_have_no_citation_and_rationale() -> None:
    for param in registry():
        if param.source_type == "assumed":
            assert param.citation is None, param.id
            assert param.rationale.strip(), param.id
            assert "assumed" in param.rationale.lower(), param.id


def test_sensor_shape_and_calibrated_have_citation() -> None:
    for param in registry():
        if param.source_type in {"sensor_shape_checked", "calibrated"}:
            assert isinstance(param.citation, str) and param.citation.strip(), param.id


def test_bounds_contain_current_value() -> None:
    for param in registry():
        assert param.low <= param.value <= param.high, param.id


def test_units_nonempty() -> None:
    assert all(param.units.strip() for param in registry())


def test_ids_unique() -> None:
    ids = [param.id for param in registry()]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)


def test_markdown_is_byte_identical() -> None:
    assert (_ROOT / "docs/PARAMETERS.md").read_bytes() == render_markdown().encode("utf-8")


def test_count_sanity() -> None:
    params = registry()
    assert any(param.source_type == "calibrated" for param in params)
    assert len(params) > 100


def test_hydraulic_overrides_are_unchanged_shape_checks() -> None:
    fit = json.loads((_ROOT / "app/sim/v2/calibration/hydraulics_fit.json").read_text(encoding="utf-8"))
    for key, value in fit["overrides"].items():
        assert value == hydraulics.PRIOR_PARAMS[key] == hydraulics.PARAMS[key]
    checked = {"pressure_noise", "temp_noise", "heat_eta", "leak_weight", "precharge_weight"}
    for key in hydraulics.PARAMS:
        expected = "sensor_shape_checked" if key in checked else "assumed"
        assert get(f"v2.models.hydraulics.PARAMS[{key}]").source_type == expected


def test_engine_and_electrical_classifications() -> None:
    for key in engine.PARAMS:
        expected = "sensor_shape_checked" if key == "accel_ref" else "assumed"
        assert get(f"v2.models.engine.PARAMS[{key}]").source_type == expected
    for key in electrical.PARAMS:
        param = get(f"v2.models.electrical.PARAMS[{key}]")
        if key in {"resistance_fade", "capacity_noise", "resistance_noise"}:
            assert param.source_type == "calibrated"
        else:
            expected = "sensor_shape_checked" if key == "resistance_rate" else "assumed"
            assert param.source_type == expected
    assert get("constants.ENGINE_CMAPSS_MEAN_LIFE_CYCLES").source_type == "sensor_shape_checked"
    assert get("constants.ENGINE_FLIGHTS_PER_MODEL_CYCLE").source_type == "assumed"


def test_existing_readers_follow_replaced_live_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    scalar = get("constants.FLIGHTS_PER_DAY")
    model = get("v2.models.engine.PARAMS[jitter_sigma]")
    policy = get("v2.world.POLICY[engine].check_days")
    fault = get("v2.faults.LIBRARY[hpt_blade_erosion].base_hazard")
    station = get("v2.drivers.STATION_ENV[ATL][0]")
    monkeypatch.setattr(constants, "FLIGHTS_PER_DAY", scalar.value + 1)
    monkeypatch.setattr(engine, "PARAMS", {**engine.PARAMS, "jitter_sigma": model.value + 0.01})
    monkeypatch.setattr(world, "POLICY", {**world.POLICY, "engine": replace(world.POLICY["engine"], check_days=12.0)})
    monkeypatch.setattr(faults, "LIBRARY", {**faults.LIBRARY, "hpt_blade_erosion": replace(faults.LIBRARY["hpt_blade_erosion"], base_hazard=0.0001)})
    monkeypatch.setattr(drivers, "STATION_ENV", {**drivers.STATION_ENV, "ATL": (55.0, 0.15)})
    assert scalar.value == constants.FLIGHTS_PER_DAY
    assert model.value == engine.PARAMS["jitter_sigma"]
    assert policy.value == 12.0
    assert fault.value == 0.0001
    assert station.value == 55.0
    assert "55" in next(line for line in render_markdown().splitlines() if line.startswith("| v2.drivers.STATION_ENV[ATL][0] |"))


def test_coupling_readers_and_coverage_follow_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    param = get("v2.coupling.engine>ecs.gain")
    original = coupling.default_edges

    def changed_edges() -> list[coupling.Edge]:
        edges = original()
        for edge in edges:
            if edge.name == "engine>ecs":
                edge.gain = 0.9
        edges.append(replace(edges[0], name="extra>edge"))
        return edges

    monkeypatch.setattr(coupling, "default_edges", changed_edges)
    assert param.value == 0.9
    assert coverage_gaps() == (["v2.coupling.extra>edge.clamp", "v2.coupling.extra>edge.gain"], [])


def test_coverage_detects_added_and_removed_numeric_leaves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(engine.PARAMS, "unreviewed", 0.125)
    monkeypatch.delitem(engine.PARAMS, "jitter_sigma")
    monkeypatch.setattr(drivers, "NEW_NUMBERS", {"record": [1.0, (2.0, True, "skip")]}, raising=False)
    monkeypatch.setattr(drivers, "NEW_FLAG", True, raising=False)
    monkeypatch.setattr(drivers, "NEW_LABELS", ["text", False], raising=False)
    monkeypatch.setitem(electrical.PRIOR_PARAMS, "unreviewed_prior", 12.0)
    assert coverage_gaps() == (
        ["v2.drivers.NEW_NUMBERS[record][0]", "v2.drivers.NEW_NUMBERS[record][1][0]", "v2.models.engine.PARAMS[unreviewed]"],
        ["v2.models.engine.PARAMS[jitter_sigma]"],
    )


def test_imported_names_and_snapshots_are_not_registered() -> None:
    ids = {param.id for param in registry()}
    assert not any("PRIOR_PARAMS" in id or id.startswith("v2.constants.") for id in ids)
    assert "v2.models.engine.ENGINE_FLIGHTS_PER_MODEL_CYCLE" not in ids
    assert "v2.world.TIRE_PARAMS[TREAD_NEW_MM]" not in ids
    assert "v2.models.engine._MASKS[hpt][T50]" in ids
    assert "v2.models.engine._SHARES[fd001][0]" in ids


def test_lookup_bounds_and_frozen_metadata() -> None:
    param = get("constants.FLIGHTS_PER_DAY")
    assert bounds(param.id) == (param.low, param.high)
    with pytest.raises(KeyError):
        get("missing")
    with pytest.raises(KeyError):
        bounds("missing")
    with pytest.raises(FrozenInstanceError):
        setattr(param, "units", "changed")
    for entry in registry():
        assert entry.source_type in {"calibrated", "sensor_shape_checked", "assumed"}
        assert "\n" not in entry.rationale
        assert "\r" not in entry.rationale


def test_markdown_deterministic_and_escaped() -> None:
    markdown = render_markdown()
    assert markdown == render_markdown()
    assert "\r" not in markdown
    for line in markdown.splitlines():
        if line.startswith("| "):
            assert len(line.split(" | ")) == 11


def test_every_entry_has_role_and_perturb_consistent() -> None:
    for param in registry():
        assert param.role in ROLES, param.id
        assert param.perturb == (param.role in {"rate", "shape", "threshold"}), param.id


def test_anchor_examples_are_fixed_and_rates_perturbable() -> None:
    for id in (
        "v2.models.electrical.PARAMS[capacity_healthy]",
        "v2.models.electrical.PARAMS[capacity_limit]",
        "v2.models.landing_gear.PARAMS[P_NOM_KPA]",
        "v2.models.brakes.PARAMS[N_BRAKES]",
        "v2.drivers.STATION_ENV[ATL][0]",
    ):
        assert get(id).perturb is False, id
    assert get("v2.models.electrical.PARAMS[cycle_fade]").role == "rate"
    assert get("v2.world.POLICY[engine].check_days").role == "threshold"
    assert get("v2.models.engine.PARAMS[jitter_sigma]").perturb is True


def test_unclassified_role_is_a_coverage_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.sim.v2 import params_registry

    monkeypatch.setattr(params_registry, "_classify", lambda id, units: ("", True))
    missing, stale = coverage_gaps()
    assert stale == [] and missing and all(item.endswith(" (role)") for item in missing)
