"""Hierarchical Weibull (MAP + Laplace) validation on synthetic fleets with known truth.

Default: 60 replications (a few seconds). ``RUN_SLOW=1`` runs the full 200. Synthetic recovery != field validity.
"""
from __future__ import annotations

import csv
import gzip
import io
import math
import os
import time
from pathlib import Path

import numpy as np
import pytest

from app.reliability.bayes import FitConfig, Record, fit, from_removal_records

N_REP = 200 if os.environ.get("RUN_SLOW") else 60
BETA, MU, SIG_T, SIG_E, SIG_B = 2.0, math.log(3000.0), 0.4, 0.2, 0.15


def sim_fleet(rng: np.random.Generator, n_tail: int | None = None, beta: float = BETA, mu: float = MU, sig_t: float = SIG_T,
              counts: np.ndarray | None = None, late_entry: float = 0.5):
    n_tail = n_tail or int(rng.integers(24, 61))
    u_t = rng.normal(0, sig_t, n_tail)
    env = rng.integers(0, 4, n_tail)
    bat = rng.integers(0, 3, n_tail)
    u_e, u_b = rng.normal(0, SIG_E, 4), rng.normal(0, SIG_B, 3)
    recs: list[Record] = []
    for i in range(n_tail):
        k = int(counts[i]) if counts is not None else int(rng.integers(1, 6))
        eta = math.exp(mu + u_t[i] + u_e[env[i]] + u_b[bat[i]])
        for _ in range(k):
            entry = rng.uniform(0, 1500) if rng.random() < late_entry else 0.0
            T = eta * ((entry / eta) ** beta - math.log(rng.random())) ** (1 / beta)  # life conditional on surviving to entry
            cens = entry + rng.uniform(300, 2500)  # heavy right-censoring
            recs.append(Record("PN-1", f"T{i}", entry, min(T, cens), bool(T <= cens), env=f"E{env[i]}", batch=f"B{bat[i]}"))
    return recs, u_t


@pytest.fixture(scope="module")
def replication():
    t0 = time.time()
    rng = np.random.default_rng(20260101)
    cov_tail, cov_beta, betas, mus, cens_frac = [], [], [], [], []
    for _ in range(N_REP):
        recs, u_t = sim_fleet(rng)
        res = fit(recs)
        p = res.params["part_numbers"]["PN-1"]
        betas.append(p["beta"])
        mus.append(p["mu"])
        cov_beta.append(abs(math.log(p["beta"] / BETA)) <= 1.6449 * p["beta_sd_log"])
        ef = res.effects("tail")
        cov_tail.append(np.mean([e["lo"] <= u_t[int(e["level"][1:])] <= e["hi"] for e in ef]))
        cens_frac.append(res.diagnostics()["n_censored"] / res.diagnostics()["n_records"])
    out = {"beta_bias": float(np.mean(betas) - BETA), "mu_bias": float(np.mean(mus) - MU), "cov_tail": float(np.mean(cov_tail)),
           "cov_beta": float(np.mean(cov_beta)), "cens": float(np.mean(cens_frac)), "secs": time.time() - t0}
    print("\nrecovery over", N_REP, "reps:", {k: round(v, 3) for k, v in out.items()})
    return out


def test_parameter_recovery_bias_small(replication):
    assert replication["cens"] > 0.5  # the harness really is heavily censored
    assert abs(replication["beta_bias"]) < 0.12  # true beta = 2.0
    assert abs(replication["mu_bias"]) < 0.1  # true mu = ln 3000 (log-eta)


def test_interval_coverage_in_band(replication):
    assert 0.85 <= replication["cov_tail"] <= 0.95, replication
    assert 0.85 <= replication["cov_beta"] <= 0.95, replication


def test_shrinkage_small_count_tails_shrink_more():
    rng = np.random.default_rng(5)
    slopes = {"small": [], "large": []}
    cred = {"small": [], "large": []}
    for _ in range(25):
        n = 40
        counts = np.where(np.arange(n) % 2 == 0, 1, 8)
        recs, u_t = sim_fleet(rng, n_tail=n, counts=counts, late_entry=0.0)
        recs = [Record(r.part_number, r.tail, r.start, r.end, r.failed) for r in recs]  # tail-only model
        ef = fit(recs, FitConfig(groups=("tail",))).effects("tail")
        for kind, sel in (("small", 1), ("large", 8)):
            rows = [e for e in ef if e["n_records"] == sel]
            truth = np.array([u_t[int(e["level"][1:])] for e in rows])
            est = np.array([e["effect"] for e in rows])
            slopes[kind].append(float(np.sum(est * truth) / np.sum(truth**2)))  # regression slope of estimate on truth
            cred[kind].extend(e["credibility"] for e in rows)
    assert np.mean(cred["small"]) < np.mean(cred["large"])
    assert np.mean(slopes["small"]) < np.mean(slopes["large"])
    assert np.mean(slopes["small"]) < 0.5  # strongly pulled to the mean


def _plain(n: int, rng: np.random.Generator, late: bool = False) -> list[Record]:
    eta = 3000.0
    out = []
    for i in range(n):
        entry = rng.uniform(0, 3000) if late else 0.0
        T = eta * ((entry / eta) ** BETA - math.log(rng.random())) ** (1 / BETA)
        cens = entry + rng.uniform(500, 2500)
        out.append(Record("P", f"T{i % 20}", entry, min(T, cens), bool(T <= cens)))
    return out


def test_censoring_handled_naive_is_biased_down():
    rng = np.random.default_rng(11)
    recs = _plain(600, rng)
    cfg = FitConfig(groups=())
    good = fit(recs, cfg).params["part_numbers"]["P"]["mu"]
    naive = fit([Record(r.part_number, r.tail, r.start, r.end, True) for r in recs], cfg).params["part_numbers"]["P"]["mu"]
    assert abs(good - math.log(3000)) < 0.1
    assert naive < good - 0.3  # treating suspensions as failures biases eta low


def test_late_entry_handled():
    rng = np.random.default_rng(12)
    recs = _plain(800, rng, late=True)
    cfg = FitConfig(groups=())
    good = fit(recs, cfg).params["part_numbers"]["P"]["mu"]
    ignore = fit([Record(r.part_number, r.tail, 0.0, r.end, r.failed) for r in recs], cfg).params["part_numbers"]["P"]["mu"]
    assert abs(good - math.log(3000)) < 0.12
    assert abs(ignore - math.log(3000)) > abs(good - math.log(3000)) + 0.1  # survivor-only data without truncation is wrong


def test_identifiability_warnings_and_guards():
    rng = np.random.default_rng(3)
    recs = _plain(60, rng)
    one_level = [Record(r.part_number, r.tail, r.start, r.end, r.failed, env="ONLY") for r in recs]
    d = fit(one_level, FitConfig(groups=("tail", "env"))).diagnostics()
    assert any("single level" in w and "env" in w for w in d["warnings"])
    # a tail with one record is flagged weak, and tiny data does not crash
    single = recs + [Record("P", "LONELY", 0.0, 1000.0, True)]
    res = fit(single, FitConfig(groups=("tail",)))
    lonely = next(e for e in res.effects("tail") if e["level"] == "LONELY")
    assert lonely["weakly_identified"] and np.isfinite(lonely["effect"]) and any("single record" in w for w in res.diagnostics()["warnings"])
    tiny = fit([Record("P", "A", 0, 100, True), Record("P", "B", 0, 200, False), Record("P", "C", 0, 150, True)], FitConfig(groups=("tail",)))
    assert all(np.isfinite(e["effect"]) and np.isfinite(e["sd"]) for e in tiny.effects("tail"))
    assert any("prior-dominated" in w for w in tiny.diagnostics()["warnings"])


def test_predictions_deterministic_and_coherent():
    rng = np.random.default_rng(8)
    recs, _ = sim_fleet(rng, n_tail=40)
    a, b = fit(recs), fit(recs)
    assert a.params == b.params
    p1, p2 = a.predict_reliability("PN-1", 1500.0, {"tail": "T3"}), b.predict_reliability("PN-1", 1500.0, {"tail": "T3"})
    assert p1 == p2
    assert 0 < p1["reliability_lo"] <= p1["reliability_hi"] < 1
    later = a.predict_reliability("PN-1", 4000.0)
    assert later["reliability"] < a.predict_reliability("PN-1", 500.0)["reliability"]
    assert later["b10"] < later["mtbur"]
    assert 2000 < later["mtbur"] < 4500  # truth: eta=3000, beta=2 -> mean ~2660
    assert a.predict_reliability("PN-1", 1000.0, {"tail": "NEW-TAIL"})["reliability_hi"] > 0  # unseen level is allowed
    with pytest.raises(KeyError):
        a.predict_reliability("NOPE", 100.0)


def test_model_vs_null_comparison():
    rng = np.random.default_rng(21)
    strong, _ = sim_fleet(rng, n_tail=40, sig_t=0.9, counts=np.full(40, 6), late_entry=0.0)
    d = fit(strong, FitConfig(groups=("tail",))).diagnostics()
    assert d["delta_aic"] > 0 and d["log_bayes_factor"] > 0 and d["effects_improve_fit"]
    flat, _ = sim_fleet(rng, n_tail=40, sig_t=0.0, counts=np.full(40, 6), late_entry=0.0)
    flat = [Record(r.part_number, r.tail, r.start, r.end, r.failed) for r in flat]
    f = fit(flat, FitConfig(groups=("tail",))).diagnostics()
    assert f["log_bayes_factor"] < d["log_bayes_factor"] and f["delta_aic"] < d["delta_aic"]


def test_gradient_matches_finite_difference():
    from app.reliability import bayes as B

    rng = np.random.default_rng(2)
    recs, _ = sim_fleet(rng, n_tail=12)
    dat, *_ = B._build(recs, FitConfig(), ("tail", "env"))
    m = B._Model(dat, FitConfig())
    P = dat.n_pn
    theta = np.r_[0.5, 0.1, rng.normal(0, 0.2, dat.X.shape[1])]
    ls = np.full(dat.n_g, -1.0)
    g = m.joint_grad(theta, ls)
    num = np.array([(m.joint_neg_log_post(theta + e, ls) - m.joint_neg_log_post(theta - e, ls)) / 2e-6 for e in np.eye(len(theta)) * 1e-6])
    assert P == 1 and np.allclose(g, num, rtol=1e-4, atol=1e-4)


def test_fit_on_simulator_output(tmp_path):
    from app.sim.v2.runner import Runner

    rn = Runner(tmp_path / "r.db", tmp_path / "runs")
    rid = rn.create(start=False, years=1.0, seed=5, fleet=8, checkpoint_every=400)
    assert rn.execute(rid)["status"] == "done"
    with gzip.open(Path(rn._row(rid)["out_dir"]) / "removals.csv.gz") as fh:
        rows = list(csv.DictReader(io.TextIOWrapper(fh, "utf-8")))
    recs = from_removal_records(rows)
    assert len(recs) > 20 and {"tail"} == {k for k in ("tail",) if any(r.tail for r in recs)}
    assert any(r.failed for r in recs) and any(not r.failed for r in recs)
    # fit the part type with the most failures
    kinds = {}
    for r in recs:
        kinds.setdefault(r.part_number, []).append(r)
    kind = max(kinds, key=lambda k: sum(r.failed for r in kinds[k]))
    res = fit(kinds[kind], FitConfig(groups=("tail",)))
    p = res.params["part_numbers"][kind]
    assert np.isfinite(p["beta"]) and np.isfinite(p["mu"])
    assert all(np.isfinite(e["effect"]) and np.isfinite(e["lo"]) and np.isfinite(e["hi"]) for e in res.effects("tail"))
    assert math.isfinite(res.predict_reliability(kind, 100.0)["mtbur"])
