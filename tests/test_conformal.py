"""Split conformal math (app/ml/conformal.py). Pure numpy, no simulator."""
import math

import numpy as np
import pytest

from app.ml import conformal as cf


def test_quantile_is_ceil_n_plus_1_th_smallest():
    s = np.arange(1, 10, dtype=float)                      # n = 9
    # alpha=0.2 -> k = ceil(10 * 0.8) = 8 -> 8th smallest = 8
    assert cf.conformal_quantile(s, 0.2) == 8.0
    # alpha=0.1 -> k = ceil(10 * 0.9) = 9 -> max
    assert cf.conformal_quantile(s, 0.1) == 9.0
    # alpha so small that k > n -> infinite interval (honest: too few calibration points)
    assert math.isinf(cf.conformal_quantile(s, 0.05))


def test_quantile_ignores_order_and_nan_and_validates():
    s = np.array([5.0, 1.0, np.nan, 3.0, 2.0, 4.0])
    assert cf.conformal_quantile(s, 0.4) == 4.0            # n=5, k=ceil(6*0.6)=4
    with pytest.raises(ValueError):
        cf.conformal_quantile(np.array([]), 0.2)
    with pytest.raises(ValueError):
        cf.conformal_quantile(s, 1.0)


def test_absolute_interval_and_coverage_guarantee():
    rng = np.random.default_rng(0)
    cal_pred = rng.normal(size=4000)
    cal_y = cal_pred + rng.normal(scale=2.0, size=4000)
    m = cf.AbsoluteConformal(0.2).fit(cal_y, cal_pred)
    te_pred = rng.normal(size=20000)
    te_y = te_pred + rng.normal(scale=2.0, size=20000)
    lo, hi = m.interval(te_pred)
    assert np.allclose(hi - lo, 2 * m.q)
    assert cf.coverage(te_y, lo, hi) == pytest.approx(0.8, abs=0.02)


def test_normalized_is_adaptive_and_covers():
    rng = np.random.default_rng(1)

    def draw(n):
        sig = rng.uniform(0.5, 5.0, size=n)
        pred = rng.normal(size=n)
        return pred + rng.normal(size=n) * sig, pred, sig

    y, p, s = draw(6000)
    m = cf.NormalizedConformal(0.2).fit(y, p, s)
    yt, pt, st = draw(20000)
    lo, hi = m.interval(pt, st)
    assert cf.coverage(yt, lo, hi) == pytest.approx(0.8, abs=0.02)
    assert np.all(np.diff((hi - lo)[np.argsort(st)]) >= -1e-9)      # wider where the spread is larger
    # the absolute variant gives the same width everywhere, so it over-covers the low-spread band
    ab = cf.AbsoluteConformal(0.2).fit(y, p)
    lo_a, hi_a = ab.interval(pt)
    small = st < 1.5
    assert cf.coverage(yt[small], lo_a[small], hi_a[small]) > cf.coverage(yt[small], lo[small], hi[small])


def test_cqr_widens_an_overconfident_band_and_covers():
    rng = np.random.default_rng(2)

    def draw(n):
        mu = rng.normal(size=n)
        y = mu + rng.normal(scale=1.0, size=n)
        return y, mu - 0.3, mu + 0.3                       # band far too narrow (true 80% band is +-1.28)

    y, lo, hi = draw(5000)
    m = cf.CQR(0.2).fit(y, lo, hi)
    assert m.q > 0.5
    yt, lot, hit = draw(20000)
    assert cf.coverage(yt, lot, hit) < 0.4
    l2, h2 = m.interval(lot, hit)
    assert cf.coverage(yt, l2, h2) == pytest.approx(0.8, abs=0.02)
    assert cf.mean_width(l2, h2) == pytest.approx(cf.mean_width(lot, hit) + 2 * m.q)


def test_unfitted_raises():
    with pytest.raises(RuntimeError):
        cf.AbsoluteConformal().interval(np.zeros(2))
    with pytest.raises(RuntimeError):
        cf.CQR().interval(np.zeros(2), np.ones(2))
