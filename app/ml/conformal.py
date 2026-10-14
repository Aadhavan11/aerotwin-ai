"""Split conformal prediction intervals (numpy only).

Three variants, all calibrated on a set that was NOT used to fit the point/quantile model:

* ``AbsoluteConformal``   : interval ``pred +/- q`` with ``q`` the finite-sample quantile of ``|y - pred|``.
* ``NormalizedConformal`` : interval ``pred +/- q * sigma(x)``; scores ``|y - pred| / sigma``; ``sigma`` is a
  per-prediction spread (e.g. the width of a quantile band) so the interval is wider where the model is unsure.
* ``CQR``                 : conformalized quantile regression (Romano et al. 2019). Scores
  ``max(lo - y, y - hi)`` of a lower/upper quantile pair; interval ``[lo - q, hi + q]``.

Guarantee (exchangeable calibration and test points): ``P(y in interval) >= 1 - alpha``. Rows of one unit are
NOT exchangeable with each other and a shifted operating regime breaks exchangeability with the calibration set,
so the guarantee is only approximate in practice; ``coverage`` exists to measure it on held-out units and regimes.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np


def conformal_quantile(scores: np.ndarray, alpha: float) -> float:
    """The ceil((n + 1)(1 - alpha))-th smallest score (the split-conformal threshold). ``inf`` if it exceeds n."""
    s = np.sort(np.asarray(scores, dtype="float64").ravel())
    s = s[~np.isnan(s)]
    n = len(s)
    if n == 0:
        raise ValueError("need at least one calibration score")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    k = math.ceil((n + 1) * (1.0 - alpha))
    if k > n:
        return float("inf")
    return float(s[k - 1])


def coverage(y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    y, lo, hi = (np.asarray(a, dtype="float64") for a in (y, lo, hi))
    return float(np.mean((y >= lo) & (y <= hi)))


def mean_width(lo: np.ndarray, hi: np.ndarray) -> float:
    return float(np.mean(np.asarray(hi, dtype="float64") - np.asarray(lo, dtype="float64")))


class AbsoluteConformal:
    name = "absolute"

    def __init__(self, alpha: float = 0.2) -> None:
        self.alpha = float(alpha)
        self.q: Optional[float] = None

    def fit(self, y: np.ndarray, pred: np.ndarray) -> "AbsoluteConformal":
        self.q = conformal_quantile(np.abs(np.asarray(y, dtype="float64") - np.asarray(pred, dtype="float64")), self.alpha)
        return self

    def interval(self, pred: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.q is None:
            raise RuntimeError("fit first")
        p = np.asarray(pred, dtype="float64")
        return p - self.q, p + self.q


class NormalizedConformal:
    name = "normalized"

    def __init__(self, alpha: float = 0.2, eps: float = 1.0) -> None:
        self.alpha = float(alpha)
        self.eps = float(eps)         # floor on sigma, in target units, so a zero-width band cannot divide by 0
        self.q: Optional[float] = None

    def _sigma(self, spread: np.ndarray) -> np.ndarray:
        return np.maximum(np.asarray(spread, dtype="float64"), self.eps)

    def fit(self, y: np.ndarray, pred: np.ndarray, spread: np.ndarray) -> "NormalizedConformal":
        r = np.abs(np.asarray(y, dtype="float64") - np.asarray(pred, dtype="float64")) / self._sigma(spread)
        self.q = conformal_quantile(r, self.alpha)
        return self

    def interval(self, pred: np.ndarray, spread: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.q is None:
            raise RuntimeError("fit first")
        p = np.asarray(pred, dtype="float64")
        w = self.q * self._sigma(spread)
        return p - w, p + w


class CQR:
    name = "cqr"

    def __init__(self, alpha: float = 0.2) -> None:
        self.alpha = float(alpha)
        self.q: Optional[float] = None

    def fit(self, y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> "CQR":
        y, lo, hi = (np.asarray(a, dtype="float64") for a in (y, lo, hi))
        self.q = conformal_quantile(np.maximum(lo - y, y - hi), self.alpha)
        return self

    def interval(self, lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.q is None:
            raise RuntimeError("fit first")
        return np.asarray(lo, dtype="float64") - self.q, np.asarray(hi, dtype="float64") + self.q
