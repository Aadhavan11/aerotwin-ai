"""Acceptance tests for feature 3.1 (FEATURES-3.1-3.6.md).

Run ``python -m pytest tests/test_ml.py -s`` to see the held-out metrics printed
for the slide. They come from ``app/ml/artifacts/eval.json`` (written by
``python -m app.ml.train``), never typed by hand.
"""
import json
from datetime import datetime, timezone

import numpy as np
import pytest

from app.contracts.models import HealthScore, TelemetryRow
from app.ml import cmapss, health
from app.ml.sensors import GENERIC, SENSOR_NAMES

TS = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
TAIL, CID = "SIM-017", "SIM-017/engine_2"

pytestmark = pytest.mark.skipif(not health.model_available(), reason="run: python -m app.ml.train")


def rows_from(traj_rows, component_id=CID):
    return [TelemetryRow(tail=TAIL, component_id=component_id, ts=TS, cycle=r["cycle"], values=dict(r["values"]))
            for r in traj_rows]


@pytest.fixture(scope="module")
def traj():
    return cmapss.trajectories()          # held-out test engines


@pytest.fixture(scope="module")
def late_unit(traj):
    # An engine whose recording ends close to failure (true RUL <= 25).
    for u, rows in traj.items():
        if rows[-1]["true_rul"] <= 25 and len(rows) >= 40:
            return u
    pytest.skip("no late-life engine available")


@pytest.fixture(scope="module")
def healthy_unit(traj):
    for u, rows in traj.items():
        if rows[-1]["true_rul"] > 100 and len(rows) >= 100:
            return u
    pytest.skip("no healthy engine available")


# ---- features --------------------------------------------------------------
def test_window_features_slope_matches_polyfit():
    t = np.arange(1, 41, dtype=float)
    v = np.stack([2.0 * t + 5.0, -0.5 * t + 100.0], axis=1)
    f = health.window_features(v, t, win=30)
    k = 2
    slope = f[-1, k:2 * k]
    assert slope == pytest.approx([2.0, -0.5], abs=1e-9)
    assert f[-1, 0:k] == pytest.approx(v[-30:].mean(axis=0))
    assert f[-1, 2 * k:3 * k] == pytest.approx(v[-1])


def test_train_and_serve_features_are_identical(traj, healthy_unit):
    rows = traj[healthy_unit]
    vals = np.array([[r["values"][s] for s in SENSOR_NAMES] for r in rows])
    cyc = np.array([r["cycle"] for r in rows], dtype=float)
    full = health.window_features(vals, cyc)[-1]
    only_last_30 = health.window_features(vals[-30:], cyc[-30:])[-1]
    assert full == pytest.approx(only_last_30, rel=1e-9, abs=1e-6)


# ---- engine scorer: normal, late-life, broken ------------------------------
def test_normal_window_is_healthy(traj, healthy_unit):
    s = health.score_engine(TAIL, CID, rows_from(traj[healthy_unit][40:70]))
    assert isinstance(s, HealthScore)
    assert s.data_quality.status == "ok", s.data_quality.reasons
    assert not s.is_anomaly
    assert s.health > 0.6
    assert s.rul_cycles.p50 > 60
    assert s.rul_cycles.p10 <= s.rul_cycles.p50 <= s.rul_cycles.p90
    assert s.model_version == health.MODEL_VERSION
    assert len(s.top_signals) == 3 and set(s.top_signals) <= set(SENSOR_NAMES)


def test_late_life_window_is_flagged(traj, late_unit):
    s = health.score_engine(TAIL, CID, rows_from(traj[late_unit][-30:]))
    assert s.rul_cycles.p50 < 40
    assert s.health < 0.4
    assert s.rul_cycles.p10 <= s.rul_cycles.p50 <= s.rul_cycles.p90


@pytest.mark.skipif(not cmapss.data_available(), reason="needs the NASA FD001 files (python -m app.ml.cmapss --download)")
def test_late_life_truth_inside_band(traj):
    """On every end-of-recording window of engines with true RUL <= 30, the truth is in [p10, p90] or close to it."""
    hits = total = 0
    for rows in traj.values():
        if rows[-1]["true_rul"] <= 30 and len(rows) >= 30:
            s = health.score_engine(TAIL, CID, rows_from(rows[-30:]))
            total += 1
            hits += s.rul_cycles.p10 - 3 <= rows[-1]["true_rul"] <= s.rul_cycles.p90 + 3
    assert total >= 5
    assert hits / total >= 0.7


def test_broken_flat_sensor_is_degraded(traj, healthy_unit):
    rows = rows_from(traj[healthy_unit][40:70])
    for r in rows:
        r.values["T50"] = 1400.0
    s = health.score_engine(TAIL, CID, rows)
    assert s.data_quality.status == "degraded"
    assert any("T50" in x and "flat" in x for x in s.data_quality.reasons)


def test_short_window_is_degraded(traj, healthy_unit):
    s = health.score_engine(TAIL, CID, rows_from(traj[healthy_unit][:5]))
    assert s.data_quality.status == "degraded"
    assert any("5 of 30" in x for x in s.data_quality.reasons)


def test_out_of_range_value_is_degraded(traj, healthy_unit):
    rows = rows_from(traj[healthy_unit][40:70])
    rows[-1].values["T50"] = 9000.0
    s = health.score_engine(TAIL, CID, rows)
    assert s.data_quality.status == "degraded"
    assert any("out of training range" in x for x in s.data_quality.reasons)


def test_mostly_missing_sensors_is_bad(traj, healthy_unit):
    rows = rows_from(traj[healthy_unit][40:70])
    rows[-1].values = {"T50": 1400.0}
    s = health.score_engine(TAIL, CID, rows)
    assert s.data_quality.status == "bad"
    assert s.rul_cycles is None


def test_score_is_deterministic_and_json_safe(traj, healthy_unit):
    rows = rows_from(traj[healthy_unit][40:70])
    a = health.score_engine(TAIL, CID, rows)
    b = health.score_engine(TAIL, CID, rows)
    assert a == b
    json.loads(a.model_dump_json())


def test_rule_fallback_when_artifacts_are_missing(monkeypatch, traj, late_unit):
    monkeypatch.setattr(health, "_bundle", lambda: None)
    s = health.score_engine(TAIL, CID, rows_from(traj[late_unit][-30:]))
    assert s.model_version == health.RULE_VERSION
    assert s.data_quality.status == "degraded"
    assert any("rule-based fallback" in x for x in s.data_quality.reasons)
    assert s.rul_cycles is not None


# ---- generic components (rule-based demo models) ---------------------------
def _generic_rows(kind, levels, cid=None):
    g = GENERIC[kind]
    cid = cid or f"SIM-005/{kind}"
    return [TelemetryRow(tail="SIM-005", component_id=cid, ts=TS, cycle=i + 1,
                         values={g.wear_signal: float(v), **{e: 50.0 for e in g.extras}})
            for i, v in enumerate(levels)]


@pytest.mark.parametrize("kind", sorted(GENERIC))
def test_generic_health_falls_as_signal_moves_to_limit(kind):
    g = GENERIC[kind]
    span = g.limit - g.healthy
    new = health.score_generic(kind, "SIM-005", f"SIM-005/{kind}",
                               _generic_rows(kind, [g.healthy + 0.05 * span + 0.0005 * span * i for i in range(30)]))
    worn = health.score_generic(kind, "SIM-005", f"SIM-005/{kind}",
                                _generic_rows(kind, [g.healthy + 0.80 * span + 0.002 * span * i for i in range(30)]))
    assert new.health > worn.health
    assert worn.rul_cycles.p10 <= worn.rul_cycles.p50 <= worn.rul_cycles.p90
    assert new.rul_cycles.p50 > worn.rul_cycles.p50
    assert new.model_version == health.GENERIC_VERSION


def test_generic_dispatch_by_component_id():
    g = GENERIC["brakes"]
    s = health.score_component(None, "SIM-005", "SIM-005/brakes", _generic_rows("brakes", [1.0 + 0.01 * i for i in range(30)]))
    assert s.model_version == health.GENERIC_VERSION
    assert s.top_signals == [g.wear_signal]


# ---- reported metrics ------------------------------------------------------
def test_eval_json_exists_and_is_printed(capsys):
    e = health.evaluate()
    for k in ("rul_mae", "rul_rmse", "anomaly_precision", "anomaly_recall", "anomaly_fpr", "n_test_engines", "data_source"):
        assert k in e
    with capsys.disabled():
        print("\n--- held-out test engines (from artifacts/eval.json) ---")
        print(f"data source        : {e['data_source']}  ({e['n_test_engines']} engines, {e['n_test_windows']} windows)")
        print(f"RUL MAE / RMSE     : {e['rul_mae']} / {e['rul_rmse']} cycles (last window, vs NASA RUL file)")
        print(f"RUL MAE late life  : {e['rul_mae_late_life']} cycles (true RUL <= 30, {e['n_late_life_windows']} windows)")
        print(f"[p10,p90] coverage : tree {e['rul_interval_coverage_tree']} -> calibrated {e['rul_interval_coverage_calibrated']} (nominal 0.80)")
        print(f"anomaly P / R / FPR: {e['anomaly_precision']} / {e['anomaly_recall']} / {e['anomaly_fpr']} (label: true RUL <= 30)")


def test_metrics_block_has_contract_keys():
    b = health.ml_metrics_block()
    for k in ("rul_mae", "rul_rmse", "anomaly_precision", "anomaly_recall", "anomaly_fpr", "n_test", "data_source"):
        assert k in b


def test_evaluation_used_held_out_engines_only():
    meta = json.loads((health.ARTIFACT_DIR / "health_meta.json").read_text())
    assert set(meta["fit_units"]).isdisjoint(meta["calib_units"])
    e = health.evaluate()
    assert e["n_test_engines"] == 100
