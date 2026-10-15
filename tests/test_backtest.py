"""Detection back-test (app/ml/backtest.py). Pure metric tests use hand-built streams; one quick end-to-end run on two modes."""
import json

import numpy as np
import pytest

from app.ml import backtest as bt


def test_lead_time_on_hand_built_stream():
    cycles = np.arange(3, 103, 5)                      # scored every 5 cycles
    alert = np.zeros(len(cycles), dtype=bool)
    alert[cycles >= 53] = True                         # first alert at cycle 53
    alert[cycles == 8] = True                          # pre-onset alert must be ignored by the lead computation
    fh_cum = np.cumsum(np.full(200, 2.0))              # 2 FH per cycle
    o = bt.unit_outcome(alert, cycles, onset=40, failure=98, fh_cum=fh_cum)
    assert o["detected"] and o["detect_cycle"] == 53
    assert o["delay_cycles"] == 13 and o["lead_cycles"] == 45 and o["lead_fh"] == pytest.approx(90.0)


def test_alert_after_failure_or_none_is_a_miss_and_no_failure_has_no_lead():
    cycles = np.arange(3, 103, 5)
    late = np.zeros(len(cycles), dtype=bool)
    late[-1] = True                                    # cycle 98 alert, failure at 60 -> not before failure
    fh = np.cumsum(np.ones(200))
    assert not bt.unit_outcome(late, cycles, 40, 60, fh)["detected"]
    o = bt.unit_outcome(late, cycles, 40, None, fh)
    assert o["detected"] and o["lead_cycles"] is None and o["delay_cycles"] == 58


def test_confusion_precision_recall_known_inputs():
    alert = np.array([1, 1, 1, 0, 0, 0, 1, 0], dtype=bool)
    label = np.array([1, 1, 0, 1, 0, 0, 0, 0], dtype=bool)
    c = bt.confusion(alert, label)
    assert c == {"tp": 2, "fp": 2, "fn": 1, "tn": 3}
    pr = bt.precision_recall(c["tp"], c["fp"], c["fn"])
    assert pr["precision"] == pytest.approx(0.5) and pr["recall"] == pytest.approx(2 / 3)
    assert pr["f1"] == pytest.approx(2 * 0.5 * (2 / 3) / (0.5 + 2 / 3))
    assert bt.precision_recall(0, 0, 0) == {"precision": None, "recall": None, "f1": None}


def test_false_alarms_per_1000fh_counts_episodes_not_windows():
    cycles = np.arange(1, 11)
    alert = np.array([0, 1, 1, 0, 0, 1, 0, 0, 1, 1], dtype=bool)   # 3 episodes, 5 alert windows
    fh = np.cumsum(np.full(10, 1.5))                               # 15 FH over the span
    fa = bt.false_alarms(alert, cycles, fh)
    assert fa["episodes"] == 3 and fa["alert_windows"] == 5 and fa["fh"] == pytest.approx(15.0)
    assert bt.false_alarms_per_1000fh(fa["episodes"], fa["fh"]) == pytest.approx(200.0)
    assert bt.false_alarms_per_1000fh(1, 0.0) is None


def test_dist_percentiles_and_empty():
    d = bt.dist(list(range(1, 101)))
    assert d["n"] == 100 and d["p50"] == pytest.approx(50.5) and d["p10"] < d["p50"] < d["p90"]
    assert bt.dist([None, None]) == {"n": 0, "p10": None, "p50": None, "p90": None}


@pytest.fixture(scope="module")
def quick():
    cfg = bt.quick_config(("slow_leak", "stuck_brake"))
    return cfg, bt.run_backtest(cfg)


def test_quick_backtest_schema(quick):
    cfg, res = quick
    assert res["schema"] == bt.SCHEMA and res["provenance"] == "SIMULATED"
    assert "same simulator generated train and test" in res["caveat"]
    assert set(res["modes"]) == {"slow_leak", "stuck_brake"} and set(res["kinds"]) == {"tires", "brakes"}
    keys = {"n_units", "n_units_failed", "n_detected", "unit_recall", "recall_before_failure", "detection_delay_cycles", "lead_time_cycles", "lead_time_fh",
            "window_precision", "window_recall", "window_f1", "confusion", "false_alarms_per_1000fh", "control"}
    for m in res["modes"].values():
        assert m["onset_cycle"] == cfg.onset and "rule" in m["scorers"]
        for s in m["scorers"].values():
            assert keys <= set(s)
            assert set(s["lead_time_cycles"]) == {"n", "p10", "p50", "p90"}
            assert s["n_units"] == cfg.n_tails * (1 if m["kind"] in ("tires", "brakes") else 2) or s["n_units"] > 0
    assert json.loads(json.dumps(res, default=float))["schema"] == bt.SCHEMA
    assert "| tires | rule |" in bt.to_markdown(res)


def test_backtest_is_deterministic(quick):
    cfg, res = quick
    again = bt.run_backtest(bt.BacktestConfig(**{**cfg.__dict__, "modes": ("slow_leak",), "scorers": ("rule",)}))
    a = again["modes"]["slow_leak"]["scorers"]["rule"]
    b = res["modes"]["slow_leak"]["scorers"]["rule"]
    assert a == b
