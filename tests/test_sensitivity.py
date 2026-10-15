"""Sensitivity harness: exact restoration, common random numbers, effect sign, quick-run schema, determinism."""
import json
import time

import pytest

from app.sim.v2 import sensitivity as sens
from app.sim.v2.models import avionics


def test_perturbation_restores_params_exactly():
    before = sens.params_fingerprint()
    ids = [s.id for s in sens.discover()]
    assert len(ids) > 100
    fac = {i: 1.3 for i in ids}
    with sens.perturbed(fac):
        assert sens.params_fingerprint() != before
    assert sens.params_fingerprint() == before


def test_restores_on_exception():
    before = sens.params_fingerprint()
    with pytest.raises(RuntimeError):
        with sens.perturbed({"engine.hot_per_c": 2.0, "policy.brakes.wear_below": 0.5, "infant.avionics.M": 1.2,
                             "false_call.apu.p_call": 3.0}):
            raise RuntimeError("boom")
    assert sens.params_fingerprint() == before


def test_unknown_id_changes_nothing():
    before = sens.params_fingerprint()
    with pytest.raises(KeyError):
        with sens.perturbed({"nope.nothing": 1.1}):
            pass
    assert sens.params_fingerprint() == before


def test_values_overridden_inside_context():
    base = avionics.PARAMS
    spec = next(s for s in sens.discover() if s.group == "avionics")
    with sens.perturbed({spec.id: 1.25}):
        assert base[spec.key] == pytest.approx(spec.base * 1.25)
    assert base[spec.key] == spec.base


def test_factor_one_is_identical_and_deterministic():
    a = sens.run_metrics(3, 2, 0.1)
    b = sens.run_metrics(3, 2, 0.1, {s.id: 1.0 for s in sens.discover()[:20]})
    assert a == b


def test_probability_clipped():
    with sens.perturbed({"false_call.apu.p_call": 10.0}):
        from app.sim.v2.world import FALSE_CALL
        assert FALSE_CALL["apu"][1] == 1.0


def test_bounds_callable_used():
    assert sens.factor_range("engine.hot_per_c", lambda i: (0.5, 2.0)) == (0.5, 2.0)
    assert sens.factor_range("engine.hot_per_c", lambda i: None) == sens.DEFAULT_RANGE


def test_higher_false_call_rate_not_fewer_removals():
    pid = "false_call.hydraulics.rate"
    lo = sum(sens.run_metrics(s, 3, 0.3, {pid: 0.1})["removals.hydraulics"] for s in (1, 2))
    hi = sum(sens.run_metrics(s, 3, 0.3, {pid: 10.0})["removals.hydraulics"] for s in (1, 2))
    assert hi >= lo


@pytest.fixture(scope="module")
def quick():
    t0 = time.perf_counter()
    res = sens.run_all(quick=True)
    return res, time.perf_counter() - t0


def test_quick_schema_and_runtime(quick, tmp_path):
    res, dt = quick
    assert dt < 60
    assert res["schema"] == sens.SCHEMA
    assert res["params_fingerprint_unchanged"] is True
    assert {"config", "caveats", "runtime_s", "tornado", "morris", "envelope"} <= set(res)
    t = res["tornado"]
    assert len(t["rows"]) == 6 and t["n_seeds"] == 2
    row = t["rows"][0]
    assert {"param", "group", "low_factor", "high_factor", "outputs"} <= set(row)
    out = row["outputs"]["removals.total"]
    assert {"low", "high", "effect", "se", "seed_sd", "indistinguishable_from_seed_noise"} <= set(out)
    for o in ("nff_share", "engine_shop_visits", "deferrals", "removals.avionics", "mtbur_fh.avionics"):
        assert o in t["baseline_mean"]
    m = res["morris"]
    assert set(m["effects"]) == {"engine", "infant", "false_call"}
    assert {"mu", "mu_star", "sigma"} <= set(m["effects"]["engine"]["removals.total"])
    p = res["envelope"]["percentiles"]["removals.total"]
    assert p["p5"] <= p["p50"] <= p["p95"]
    paths = sens.write_outputs(res, str(tmp_path))
    assert json.loads((tmp_path / "sensitivity.json").read_text())["schema"] == sens.SCHEMA
    assert (tmp_path / "SUMMARY.md").exists()
    svg = (tmp_path / "tornado_removals_total.svg").read_text()
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert len(paths) > 5


def test_quick_deterministic(quick):
    res, _ = quick
    again = sens.run_all(quick=True, overrides={"max_params": 2, "morris_groups": ("avionics",), "envelope_n": 2})
    first_two = res["tornado"]["rows"][:2]
    assert [r["outputs"]["removals.total"]["effect"] for r in again["tornado"]["rows"]] == \
        [r["outputs"]["removals.total"]["effect"] for r in first_two]


def test_anchors_excluded_by_default_and_included_with_flag():
    from app.sim.v2 import params_registry as reg

    allspecs = sens.discover()
    kept, excluded, used = sens.drop_anchors(allspecs)
    assert used is True and excluded > 0 and len(kept) == len(allspecs) - excluded
    ids = {s.id for s in kept}
    for anchor in ("electrical.capacity_healthy", "landing_gear.P_NOM_KPA", "apu.healthy_margin_c"):
        assert anchor in {s.id for s in allspecs} and anchor not in ids
    flags = {p.id: p.perturb for p in reg.registry()}
    assert all(flags.get(sens.registry_id(s), True) for s in kept)
    again, none_excluded, _ = sens.drop_anchors(allspecs, include_anchors=True)
    assert none_excluded == 0 and len(again) == len(allspecs)


def test_registry_unavailable_warns(monkeypatch):
    monkeypatch.setattr(sens, "_registry_perturb", lambda: None)
    specs = sens.discover()
    kept, excluded, used = sens.drop_anchors(specs)
    assert (len(kept), excluded, used) == (len(specs), 0, False)
    res = sens.run_all(quick=True, overrides={"max_params": 1, "morris_groups": ("avionics",), "envelope_n": 1, "seeds": (1,)})
    assert res["registry_used"] is False and "registry unavailable: anchors may be included" in res["warnings"]


def test_quick_reports_anchor_metadata(quick):
    res, _ = quick
    assert res["registry_used"] is True and res["excluded_anchor_count"] > 0
    assert res["config"]["include_anchors"] is False
    assert res["warnings"] == []
    assert not {r["param"] for r in res["tornado"]["rows"]} & {"electrical.capacity_healthy", "landing_gear.P_NOM_KPA"}
