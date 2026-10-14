"""Sim-trained health models for non-engine kinds (app/ml/train_generic.py). All data are SIMULATED (v2 World); train
and test come from the same simulator, so these tests check mechanics and calibration, not real-world accuracy."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.contracts.models import TelemetryRow
from app.ml import health
from app.ml import train_generic as tg
from app.ml.sensors import GENERIC, TELEMETRY_KEYS

TS = datetime(2028, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def trained():
    cfg = tg.tiny_config()
    bundles, meta = tg.train_all(cfg)
    return cfg, bundles, meta


def test_window_features_match_numpy():
    t = np.arange(40, dtype=float)
    v = np.stack([2.0 * t + 5.0, -0.5 * t + 100.0], axis=1)
    f = tg.window_features(v, win=30)
    for c in range(2):
        mean, slope, last, std, mn, mx = f[-1, c * 6:(c + 1) * 6]
        w = v[-30:, c]
        assert mean == pytest.approx(w.mean())
        assert slope == pytest.approx(np.polyfit(np.arange(30), w, 1)[0])
        assert last == v[-1, c] and std == pytest.approx(w.std()) and mn == w.min() and mx == w.max()
    assert f[0, 1] == 0.0     # a one-row window has no slope


def test_window_features_tolerate_missing_values():
    v = np.stack([np.linspace(0, 10, 50), np.linspace(5, 1, 50)], axis=1)
    v[10:14, 0] = np.nan
    v[-2:, 0] = np.nan
    f = tg.window_features(v)
    assert not np.isnan(f[-1, 0:6]).any()
    assert f[-1, 2] == v[47, 0]          # last = last non-missing value


def test_simulation_and_fit_are_deterministic():
    cfg = tg.GenConfig(train=(), calib=(), test=(), shift=(), kinds=("tires",), n_tails=4, horizon=300, max_iter=20)
    spec = tg.RunSpec(7, maintain=False)
    a, b = tg.simulate_run(spec, cfg), tg.simulate_run(spec, cfg)
    assert all(np.array_equal(x.values, y.values, equal_nan=True) and np.array_equal(x.hi, y.hi) for x, y in zip(a, b))
    s = tg.build_samples(a, "tires", 2)
    assert len(s) > 100
    m1 = tg.fit_kind("tires", s, s, cfg)
    m2 = tg.fit_kind("tires", s, s, cfg)
    p1, p2 = tg.predict(m1, s.X), tg.predict(m2, s.X)
    assert all(np.array_equal(p1[k], p2[k]) for k in p1)


def test_splits_are_by_seed_and_unit(trained):
    cfg, _, meta = trained
    c = meta["config"]
    groups = [set(c[k]) for k in ("train_seeds", "calib_seeds", "test_seeds", "shift_seeds")]
    assert sum(len(g) for g in groups) == len(set().union(*groups))     # disjoint seeds -> disjoint unit serials
    small = tg.GenConfig(train=cfg.train[:1], calib=cfg.calib[:1], test=cfg.test[:1], shift=(), kinds=("tires",),
                         n_tails=2, horizon=60, maintained_horizon=60)
    data = tg.generate(small)
    keys = {r: {x.key for x in data[r]} for r in ("train", "calib", "test")}
    assert not (keys["train"] & keys["calib"]) and not (keys["train"] & keys["test"]) and not (keys["calib"] & keys["test"])


def test_conformal_coverage_on_held_out_units(trained):
    """80% target on held-out units. The tiny configuration has ~10-15 units per kind, so each kind is checked loosely
    and the row-weighted pool within 80 +- 8%."""
    _, _, meta = trained
    tot = cov = 0.0
    for kind, e in meta["evaluation"].items():
        t = e["test_in_distribution"]
        assert t["n_units"] >= 5
        assert 0.65 <= t["coverage_80"]["cqr"] <= 0.95, (kind, t["coverage_80"])
        assert t["rul"]["mae"] < 60
        tot += t["n_rows"]
        cov += t["n_rows"] * t["coverage_80"]["cqr"]
    assert 0.72 <= cov / tot <= 0.88


def test_shifted_regime_is_reported_not_hidden(trained):
    _, _, meta = trained
    for e in meta["evaluation"].values():
        s = e["test_shifted_regime"]
        assert s["n_rows"] > 0 and "cqr" in s["coverage_80"]
    assert meta["data"].startswith("SIMULATED")


def test_flag_default_is_rule_model():
    row = TelemetryRow(tail="T", component_id="T/brakes", ts=TS, cycle=1, values={"pad_wear_mm": 3.0, "disc_temp_c": 200.0})
    s = health.score_generic("brakes", "T", "T/brakes", [row])
    assert s.model_version == health.GENERIC_VERSION and s.explanation is None


def _window(kind, n=30, drop=()):
    keys = TELEMETRY_KEYS[kind]
    spec = GENERIC[kind]
    rows = []
    for i in range(n):
        frac = 0.2 + 0.4 * i / max(n - 1, 1)
        v = {keys[0]: spec.healthy + (spec.limit - spec.healthy) * frac}
        for k in keys[1:]:
            v[k] = 50.0 + i * 0.1
        for d in drop:
            v.pop(d, None)
        rows.append(TelemetryRow(tail="T", component_id=f"T/{kind}", ts=TS + timedelta(hours=i), cycle=100 + i, values=v))
    return rows


def test_serving_with_flag(trained, monkeypatch):
    _, bundles, _ = trained
    monkeypatch.setenv("HEALTH_MODEL", "simtrained")
    monkeypatch.setattr(tg, "load", lambda kind, directory=None: bundles.get(kind))
    s = health.score_generic("hydraulics", "T", "T/hydraulics", _window("hydraulics"))
    assert s.model_version == "sim-trained (SIMULATED data)"
    assert s.rul_cycles is not None and 0 <= s.rul_cycles.p10 <= s.rul_cycles.p50 <= s.rul_cycles.p90 <= tg.CAP
    assert 0 <= s.health <= 1 and 0 <= s.anomaly_score <= 1
    assert s.explanation and len(s.explanation) <= 3
    assert set(s.top_signals) <= set(TELEMETRY_KEYS["hydraulics"])
    assert s.data_quality.status in ("ok", "degraded")


def test_missing_channel_falls_back_with_reason(trained, monkeypatch):
    _, bundles, _ = trained
    monkeypatch.setenv("HEALTH_MODEL", "simtrained")
    monkeypatch.setattr(tg, "load", lambda kind, directory=None: bundles.get(kind))
    extra = TELEMETRY_KEYS["hydraulics"][1]
    s = health.score_generic("hydraulics", "T", "T/hydraulics", _window("hydraulics", drop=(extra,)))
    assert s.model_version == health.GENERIC_VERSION
    assert s.data_quality.status != "ok" and any("sim-trained" in r and extra in r for r in s.data_quality.reasons)
    # a kind without a fitted model also falls back
    s2 = health.score_generic("brakes", "T", "T/brakes", _window("brakes"))
    assert s2.model_version == health.GENERIC_VERSION and any("sim-trained" in r for r in s2.data_quality.reasons)


def test_save_and_load_roundtrip(trained, tmp_path):
    _, bundles, meta = trained
    sizes = tg.save(bundles, meta, tmp_path)
    assert (tmp_path / tg.META_NAME).is_file() and sum(sizes.values()) < 3_000_000
    tg.clear_cache()
    b = tg.load("tires", str(tmp_path))
    X = bundles["tires"]["baseline"].reshape(1, -1)
    assert np.allclose(tg.predict(b, X)["p50"], tg.predict(bundles["tires"], X)["p50"])
    tg.clear_cache()
