"""Calibration tests: the committed fit always loads; the fit script itself runs only when C-MAPSS data is on disk."""

from __future__ import annotations

import numpy as np
import pytest

from app.calib import engine as calib
from app.ml.sensors import SENSOR_NAMES


def test_committed_fit_loads_and_is_sane() -> None:
    fit = calib.load_fit()
    assert fit["sensors"] == list(SENSOR_NAMES)
    assert set(fit["variants"]) == {"fd001", "fd002", "fd003", "fd004"}
    nb = len(calib.BASIS_POWERS)
    for name, v in fit["variants"].items():
        assert np.asarray(v["coef_mean"]).shape == (14, nb), name
        assert np.asarray(v["coef_factors"]).shape == (calib.N_FACTORS, 14, nb)
        assert np.asarray(v["mu"]).shape == (calib.N_COND, 14)
        assert (np.asarray(v["sd"]) > 0).all()
        assert (np.asarray(v["noise_sigma"]) >= 0).all()
        assert abs(sum(v["cond_freq"]) - 1.0) < 1e-6
        assert 100 < v["life_mean"] < 400
    assert fit["variants"]["fd001"]["n_conditions"] == 1
    assert fit["variants"]["fd002"]["n_conditions"] == calib.N_COND
    assert 195 < fit["variants"]["fd001"]["life_mean"] < 215


def test_basis_shape() -> None:
    assert calib.basis(0.0).shape == (len(calib.BASIS_POWERS),)
    assert calib.basis(np.linspace(0, 1, 5)).shape == (5, len(calib.BASIS_POWERS))


def test_fit_from_data_matches_committed_fit() -> None:
    if not (calib.DATA_DIR / "train_FD001.txt").is_file():
        pytest.skip("C-MAPSS data not on disk")
    df = calib.load_train(1)
    v = calib.fit_variant(df, multi=False)
    committed = calib.load_fit()["variants"]["fd001"]
    assert v["n_engines"] == 100
    assert abs(v["life_mean"] - committed["life_mean"]) < 1e-6
    assert np.allclose(v["coef_mean"], committed["coef_mean"], atol=1e-6)
    assert np.allclose(v["noise_sigma"], committed["noise_sigma"], atol=1e-6)
