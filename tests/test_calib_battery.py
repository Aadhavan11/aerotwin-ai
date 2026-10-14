"""Battery calibration: the committed fit always loads; the fit script runs only when the NASA cells are on disk."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.calib import battery as calib
from app.sim.v2.models import electrical

needs_data = pytest.mark.skipif(not calib.data_available(), reason="NASA battery data not on disk")


def test_committed_fit_loads_and_is_sane() -> None:
    fit = calib.load_fit()
    assert "not aircraft" in fit["meta"]["tag"]
    assert set(fit["meta"]["cells"]) == set(calib.CELLS)
    assert fit["meta"]["best_capacity_model"] in calib.CANDIDATES
    cap = fit["capacity_fade"]
    for name in calib.CANDIDATES:
        for cell in calib.CELLS:
            assert 0.0 < cap["per_cell"][name][cell]["rmse_pp"] < 6.0
            assert cap["loco_cycles"][name][cell]["rmse_pp"] < 20.0
    # held-out error is larger than in-sample error: the cells genuinely differ
    assert cap["summary"]["linear"]["loco_cycles_rmse_mean_pp"] > cap["summary"]["linear"]["per_cell_rmse_mean_pp"]
    for cell in calib.CELLS:
        assert 0.3 < fit["shape"][cell]["half_window_share"] < 0.8
    assert fit["temperature_acceleration"]["supported"] is False
    assert fit["temperature_acceleration"]["ambient_c_values"] == [24.0]


def test_overrides_are_applied_to_model_params_and_rates_stay_priors() -> None:
    fit = calib.load_fit()
    for key, value in fit["overrides"].items():
        assert key in electrical.PARAMS
        assert electrical.PARAMS[key] == pytest.approx(value)
    for key in ("cycle_fade", "calendar_fade", "life_cycles", "activation_k", "failure_median_accel", "capacity_limit"):
        assert key not in fit["overrides"]
        assert electrical.PARAMS[key] == electrical.PRIOR_PARAMS[key]
    assert 0.0 < fit["overrides"]["resistance_fade"] < electrical.PRIOR_PARAMS["resistance_fade"]


def test_apply_fit_hook_handles_missing_file_and_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    params = dict(electrical.PARAMS)
    monkeypatch.setattr(electrical, "PARAMS", params)
    electrical._apply_fit(tmp_path / "absent.json")
    assert params == electrical.PARAMS
    path = tmp_path / "fit.json"
    path.write_text(json.dumps({"overrides": {"resistance_fade": 0.5, "bogus": 1}}))
    electrical._apply_fit(path)
    assert params["resistance_fade"] == 0.5 and "bogus" not in params


def test_candidate_curves_start_at_zero_and_increase() -> None:
    k = np.linspace(0.0, 150.0, 31)
    for name, (fn, p0, _) in calib.CANDIDATES.items():
        y = fn(k, *p0)
        assert abs(float(y[0])) < 1e-9, name
        assert np.all(np.diff(y) >= -1e-9), name


def test_curve_fit_recovers_synthetic_linear_fade() -> None:
    k = np.arange(100.0)
    p = calib.fit_curve("linear", k, 0.2 * k)
    assert p[0] == pytest.approx(0.2, rel=1e-3)


@needs_data
def test_fit_from_data_matches_committed_fit() -> None:
    fit = calib.fit_all()
    committed = calib.load_fit()
    assert fit["meta"]["best_capacity_model"] == committed["meta"]["best_capacity_model"]
    assert fit["overrides"] == pytest.approx(committed["overrides"])
    assert fit["resistance"]["cell_slope_mean"] == pytest.approx(committed["resistance"]["cell_slope_mean"])


@needs_data
def test_load_cells_shapes() -> None:
    cells = calib.load_cells()
    assert len(cells["B0005"]["k"]) == 168 and len(cells["B0018"]["k"]) == 132
    assert abs(float(cells["B0005"]["fade_pp"][:3].mean())) < 1.0
    assert cells["B0005"]["fade_pp"][-1] > 15.0
