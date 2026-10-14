"""Hydraulic calibration: the committed fit always loads; the fit script runs only when the UCI rig data is on disk."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.calib import hydraulic as calib
from app.sim.v2.models import hydraulics

needs_data = pytest.mark.skipif(not calib.data_available(), reason="UCI hydraulic data not on disk")


def test_committed_fit_loads_and_is_sane() -> None:
    fit = calib.load_fit()
    assert set(fit["sensors"]) == set(calib.SENSORS)
    assert set(fit["detectability"]) == set(calib.LABELS)
    assert "test rig" in fit["meta"]["tag"]
    assert fit["meta"]["n_cycles"] == 2205
    for name, s in fit["sensors"].items():
        assert set(s["coef_per_full_severity"]) == set(calib.LABELS), name
        assert s["within_cell_sd"] > 0 and s["cell_sd_median"] >= 0, name
    # the rig drives the cooler; temperature must respond strongly and positively
    assert fit["sensors"]["TS1"]["coef_per_full_severity"]["cooler"] > 5.0
    assert fit["sensors"]["PS5"]["coef_per_full_severity"]["cooler"] < 0.0


def test_overrides_are_applied_to_model_params_and_priors_are_kept() -> None:
    fit = calib.load_fit()
    for key, value in fit["overrides"].items():
        assert key in hydraulics.PARAMS
        assert hydraulics.PARAMS[key] == pytest.approx(value)
    for key in ("eta_drift", "leak_scale", "precharge_rate", "acid_rate", "life_cycles", "failure_median_accel"):
        assert key not in fit["overrides"]  # no time-to-failure information in a stepped test rig
        assert hydraulics.PARAMS[key] == hydraulics.PRIOR_PARAMS[key]


def test_apply_fit_hook_handles_missing_file_and_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    params = dict(hydraulics.PARAMS)
    monkeypatch.setattr(hydraulics, "PARAMS", params)
    hydraulics._apply_fit(tmp_path / "absent.json")
    assert params == hydraulics.PARAMS and params["temp_noise"] == hydraulics.PARAMS["temp_noise"]
    path = tmp_path / "fit.json"
    path.write_text(json.dumps({"overrides": {"temp_noise": 9.5, "not_a_param": 1.0}}))
    hydraulics._apply_fit(path)
    assert params["temp_noise"] == 9.5
    assert "not_a_param" not in params


def test_severities_map_labels_to_unit_interval() -> None:
    prof = np.array([[100, 100, 0, 130, 0], [3, 73, 2, 90, 0], [20, 90, 1, 115, 1]], dtype=float)
    s = calib.severities(prof)
    assert np.allclose(s[0], 0.0)
    assert np.allclose(s[1], 1.0)
    assert 0.0 < s[2].min() and s[2].max() < 1.0


def test_distances_zero_for_identical_samples() -> None:
    x = np.random.default_rng(0).standard_normal(500)
    d = calib.distances(x, x.copy())
    assert d["ks"] == 0.0 and d["wasserstein_over_real_sd"] == 0.0


@needs_data
def test_fit_from_data_matches_committed_fit() -> None:
    fit = calib.fit_all()
    committed = calib.load_fit()
    for name in ("PS1", "TS1", "VS1"):
        for lab in calib.LABELS:
            assert fit["sensors"][name]["coef_per_full_severity"][lab] == pytest.approx(committed["sensors"][name]["coef_per_full_severity"][lab], rel=1e-6, abs=1e-9)
    assert fit["overrides"] == pytest.approx(committed["overrides"])


@needs_data
def test_validation_runs_and_reports_distances() -> None:
    v = calib.validate(n_sim=40)
    for case in ("prior", "fitted"):
        for res in v["cases"][case].values():
            assert 0.0 <= res["ks"] <= 1.0
            assert res["wasserstein_over_real_sd"] >= 0.0
