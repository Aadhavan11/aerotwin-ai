"""ML validity pack (app/ml/validity.py): pure audit helpers plus one quick end-to-end run (tiny training, SIMULATED data)."""
import json

import numpy as np
import pytest

from app.ml import validity as v


def test_split_audit_catches_a_deliberately_leaked_split():
    ok = v.assert_disjoint({"train": ["a", "b"], "test": ["c"]})
    assert ok["disjoint"]
    with pytest.raises(AssertionError, match="leakage"):
        v.assert_disjoint({"train": ["a", "b", "c"], "test": ["c", "d"]})
    rep = v.audit_split({"train": ["a", "b"], "calib": ["b"], "test": ["x"]})
    assert rep["overlaps"] == {"train&calib": 1, "train&test": 0, "calib&test": 0} and not rep["disjoint"]


def test_binned_r2_detects_deterministic_labels():
    x = np.linspace(0, 1, 500)
    assert v.binned_r2(x, 3 * x + 1) > 0.99
    rng = np.random.default_rng(0)
    assert v.binned_r2(x, rng.normal(size=500)) < 0.2
    assert v.spearman(x, -x) == pytest.approx(-1.0)


def test_paired_bootstrap_ci_brackets_known_difference_and_is_seeded():
    rng = np.random.default_rng(1)
    groups = np.repeat(np.arange(30), 10)
    d = 5.0 + rng.normal(size=300)
    a = v.paired_bootstrap_ci(d, groups, n_boot=400, seed=3)
    assert a["lo"] < a["mean"] < a["hi"] and abs(a["mean"] - 5.0) < 0.3 and a["lo"] > 0 and a["p_positive"] == 1.0
    assert a == v.paired_bootstrap_ci(d, groups, n_boot=400, seed=3)
    z = v.paired_bootstrap_ci(rng.normal(size=300), groups, n_boot=400, seed=3)
    assert z["lo"] < 0 < z["hi"]


def test_reliability_and_coverage_by_bin_known_values():
    y = np.array([10.0, 30.0, 60.0, 150.0, 400.0])
    assert v.quantile_reliability(y, np.array([20.0, 20, 70, 100, 400]), 0.5) == pytest.approx(0.6)
    assert v.quantile_reliability(y, np.array([20.0, 20, 70, 100, 400]), 0.5, below_cap_only=True) == pytest.approx(0.5)
    b = v.coverage_by_bin(y, y - 1, y + 1)
    assert [x["n"] for x in b] == [1, 1, 1, 1, 0, 1] and all(x["coverage"] in (1.0, None) for x in b)


@pytest.fixture(scope="module")
def quick_res():
    return v.run_validity(v.quick_config(), aged_seeds=(501,), n_boot=100)


def test_quick_pack_schema_and_split_hygiene(quick_res):
    r = quick_res
    assert r["schema"] == v.SCHEMA and "same simulator generated train and test" in r["caveat"]
    k = r["kinds"]["tires"]
    assert {"leakage", "splits", "calibration", "regimes", "rule_vs_sim", "rows"} <= set(k)
    assert {"full", "no_age", "no_wear_channel", "extras_only", "label_determinism"} <= set(k["leakage"])
    assert k["splits"]["seed_grouped"]["unit_disjoint"] and k["splits"]["seed_grouped"]["seed_disjoint"]
    assert k["splits"]["unit_grouped"]["unit_disjoint"] and k["splits"]["temporal"]["unit_disjoint"]
    assert not k["splits"]["row_random_LEAKY"]["unit_disjoint"]          # the audit sees the leak in the control scheme
    assert set(k["regimes"]) == {"in_distribution", "held_out_stress_regime", "ageing_fleet"}
    cal = k["calibration"]["test"]
    assert {p["name"] for p in cal["reliability"]} == {"raw p10", "raw p50", "raw p90", "cqr p10", "cqr p90"}
    assert len(cal["coverage_by_rul_bin"]) == len(v.RUL_BINS)
    assert json.loads(json.dumps(r, default=float))["schema"] == v.SCHEMA


def test_outputs_written(quick_res, tmp_path):
    paths = v.write_outputs(quick_res, tmp_path)
    names = {p.name for p in paths}
    assert {"validity.json", "validity.md", "calibration_tires_test.svg"} <= names
    svg = (tmp_path / "calibration_tires_test.svg").read_text()
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>") and "SIMULATED" in svg
    assert "Leakage audit" in (tmp_path / "validity.md").read_text()
