"""Serialised rotable layer: lineage invariants, determinism, rogue ping-pong, NFF, shop statistics, hidden truth, ingest."""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import numpy as np
import pytest

from app.ingest import PartInstallEvent, PartRemovalEvent, validate_records
from app.insights import reliability_pack as pack
from app.sim.v2 import rotables as rot
from app.sim.v2.rotables import SWAP_ACTIONS, RotableConfig, install_rows, removal_rows
from app.sim.v2.runner import build_world
from app.sim.v2.world import World

TAILS = [f"SIM-{i:03d}" for i in range(1, 7)]
# Accelerated: many latent defects and a short rogue hazard so ping-pong shows up inside a short run.
FAST = dict(rogue_prob=0.3, rogue_hazard0=0.02, rogue_floor=0.002, rogue_tau=400.0, ber_prob=0.0, spares_per_station=1)


def make(seed: int = 5, cfg: RotableConfig | None = None, tails: list[str] = TAILS, **kw: Any) -> World:
    return World(seed, tails, stochastic=False, rotables=cfg or RotableConfig(**FAST), **kw)


@pytest.fixture(scope="module")
def long_world() -> World:
    w = make(seed=5)
    w.run(2500)
    return w


def _swaps(w: World) -> list[Any]:
    return [r for r in w.removals() if r.action in SWAP_ACTIONS and r.shop_finding is not None]


def test_default_world_has_no_rotables_and_unchanged_records() -> None:
    w = World(3, TAILS[:2], stochastic=False)
    w.run(50)
    assert w.rotables is None and w.rotable_truth() == []
    assert all(r.part_no == "" and r.shop_finding is None and r.installed_at is None for r in w.removals())
    assert all("shop_finding" not in r.as_dict() for r in w.removals())


def test_lineage_invariants(long_world: World) -> None:
    w = long_world
    reg, pool = w.registry, w.rotables
    assert pool is not None
    swaps = _swaps(w)
    assert len(swaps) > 20
    # a serial sits in at most one position and every position holds exactly one serial
    assert len(set(reg.installed.values())) == len(reg.installed) == sum(w.counts.values())
    assert {s for s, p in reg.parts.items() if p.status == "installed"} == set(reg.installed.values())
    # installs == initial population + physical removals (each removal is immediately followed by one install)
    assert len(pool.install_log) == sum(w.counts.values()) + len(swaps)
    # per-serial: intervals [install, removal) are ordered and never overlap; removal never precedes install
    removals_by_serial: dict[str, list[Any]] = defaultdict(list)
    for r in swaps:
        removals_by_serial[r.serial].append(r)
    installs_by_serial: dict[str, list[Any]] = defaultdict(list)
    for i in pool.install_log:
        installs_by_serial[i.serial].append(i)
    for serial, rs in removals_by_serial.items():
        ins = sorted(installs_by_serial[serial], key=lambda x: x.installed_at)
        rs = sorted(rs, key=lambda x: x.time)
        assert len(ins) in (len(rs), len(rs) + 1)           # last install may still be open
        for k, r in enumerate(rs):
            assert datetime.fromisoformat(r.installed_at) <= datetime.fromisoformat(r.time)
            assert r.installed_at == ins[k].installed_at
            assert r.tail == ins[k].tail and r.position == ins[k].position
            if k + 1 < len(ins):                             # removal before the next install of the same serial
                assert datetime.fromisoformat(r.time) <= datetime.fromisoformat(ins[k + 1].installed_at)
    for v in pool.shop_visits:
        assert datetime.fromisoformat(v.shop_out) >= datetime.fromisoformat(v.shop_in)
        assert v.turnaround_days >= 0.0
    # every part number is fictional and carries the kind
    assert all(p.part_no.startswith(f"PN-{p.kind.upper()}-") for p in reg.parts.values())


def test_serials_actually_move_between_tails(long_world: World) -> None:
    tails_per_serial = defaultdict(set)
    for i in long_world.rotables.install_log:
        tails_per_serial[i.serial].add(i.tail)
    assert max(len(t) for t in tails_per_serial.values()) >= 3


def test_determinism_and_resume_bit_identity() -> None:
    a = make(seed=11)
    a.run(500)
    b = make(seed=11)
    b.run(500)
    assert a.state_hash() == b.state_hash()
    c = make(seed=11)
    c.run(230)
    snap = {k: np.array(v, copy=True) for k, v in c.snapshot().items()}
    d = make(seed=11)
    d.restore(snap)
    d.run(270)
    assert d.state_hash() == a.state_hash()
    assert [r.as_dict() for r in d.removals()] == [r.as_dict() for r in a.removals()]
    assert d.rotable_truth() == a.rotable_truth()


def test_rogue_ping_pong_spans_three_tails(long_world: World) -> None:
    rogue = {t["serial"] for t in long_world.rotable_truth() if t["rogue"]}
    assert rogue
    tails: dict[str, set[str]] = defaultdict(set)
    for r in _swaps(long_world):
        if r.serial in rogue and r.removal_reason in ("failure", "inspection_finding"):
            tails[r.serial].add(r.tail)
    assert max((len(t) for t in tails.values()), default=0) >= 3, {s: sorted(t) for s, t in tails.items()}


def test_nff_returns_same_serial_unchanged(long_world: World) -> None:
    pool = long_world.rotables
    visits = {(v.serial, v.shop_in): v for v in pool.shop_visits}
    checked = 0
    for r in _swaps(long_world):
        if r.shop_finding != "nff":
            continue
        later = [i for i in pool.install_log if i.serial == r.serial and datetime.fromisoformat(i.installed_at) >= datetime.fromisoformat(r.time)]
        v = [x for x in pool.shop_visits if x.serial == r.serial and x.shop_in == r.time]
        assert v and not v[0].scrapped
        if later:
            nxt = min(later, key=lambda i: i.installed_at)
            assert (nxt.tsn_h, nxt.csn) == (r.tsn_h, r.csn)     # no hours accrue in the shop and none are reset
            checked += 1
    assert checked > 0 and visits


def test_shop_finding_statistics_match_priors() -> None:
    n_rogue = k_rogue = 0
    for seed in (1, 2, 3, 4):
        w = make(seed=seed)
        w.run(2500)
        truth = {t["serial"]: t["rogue"] for t in w.rotable_truth()}
        for r in _swaps(w):
            if r.removal_reason == "nff":
                assert r.shop_finding == "nff"          # the false-call model's own output is never "confirmed"
            elif r.removal_reason == "failure" and not truth[r.serial]:
                assert r.shop_finding == "confirmed"    # healthy-lineage serials: failures are reproduced at the shop
            elif r.removal_reason == "failure" and truth[r.serial]:
                n_rogue += 1
                k_rogue += r.shop_finding == "nff"
    assert n_rogue >= 60
    assert abs(k_rogue / n_rogue - rot.ROGUE_NFF_PROB) < 0.12


def test_mod_status_hazard_shift_direction() -> None:
    def mean_hi(post: float) -> float:
        cfg = RotableConfig(post_mod_fraction=post, quality_sigma=0.0, rogue_prob=0.0)
        w = World(3, TAILS[:3], stochastic=False, coupling=False, faults=False, auto_maintain=False, rotables=cfg)
        w.run(1500)
        return float(np.mean([w.fleet.state[k]["hi"].mean() for k in ("avionics", "hydraulics", "electrical", "apu", "ecs")]))
    assert rot.MOD_HAZARD["post"] < rot.MOD_HAZARD["pre"]
    assert mean_hi(1.0) > mean_hi(0.0)
    w = make()
    assert {p.mod_status for p in w.registry.parts.values() if p.kind == "avionics"} <= {"pre", "post"}
    assert {p.part_no for p in w.registry.parts.values() if p.kind == "avionics"} <= {"PN-AVIONICS-01", "PN-AVIONICS-02"}


def test_ground_truth_hidden_from_telemetry_records_and_pack(long_world: World) -> None:
    w = make(seed=9)
    rows = w.run(60)
    for row in rows:
        assert set(row) == {"tail", "component_id", "cycle", "ts", "values"}
        assert not any("rogue" in k or "quality" in k for k in row["values"])
    assert any(t["rogue"] for t in w.rotable_truth())
    visible = json.dumps([r.as_dict() for r in long_world.removals()] + removal_rows(long_world) + install_rows(long_world)
                         + [p.as_dict() for p in long_world.registry.parts.values()] + [v.as_dict() for v in long_world.rotables.shop_visits])
    assert "rogue" not in visible and "quality" not in visible
    out = json.dumps(pack.compute(pack.from_world(long_world)), default=str)
    assert "rogue" not in out and "quality" not in out
    assert set(long_world.rotable_truth()[0]) >= {"serial", "rogue", "quality"}   # only the separate truth export has them


def test_ingest_round_trip_serial_continuity(long_world: World) -> None:
    tz = datetime.fromisoformat(long_world.removals()[0].time).tzinfo
    assert tz is not None
    rem = [PartRemovalEvent(source="sim", provenance="SIMULATED", **{**r, "installed_at": datetime.fromisoformat(r["installed_at"]),
                                                                       "removed_at": datetime.fromisoformat(r["removed_at"])})
           for r in removal_rows(long_world)]
    ins = [PartInstallEvent(source="sim", provenance="SIMULATED", **{**i, "installed_at": datetime.fromisoformat(i["installed_at"])})
           for i in install_rows(long_world)]
    assert rem and len(ins) == len(rem) + sum(long_world.counts.values())
    report = validate_records(removals=rem, installs=ins)
    checks = {c.name: c for c in report.checks}
    assert checks["serial_double_install"].n_failed == 0
    assert checks["removal_before_install"].n_failed == 0
    assert checks["overlapping_position_installs"].n_failed == 0
    assert checks["nff_shop_consistency"].n_failed == 0
    assert checks["missing_serials"].n_failed == 0


def test_runner_export_has_lineage_columns() -> None:
    w = build_world({"seed": 3, "fleet": 3, "rotables": True})
    assert w.rotables is not None
    w.run(200)
    assert all(r.part_no for r in _swaps(w))


def test_stockout_rush_is_counted_as_exposure() -> None:
    cfg = RotableConfig(spares_per_station=1, rogue_prob=0.0, turnaround_scale=50.0, ber_prob=0.0)
    w = make(cfg=cfg, seed=2)
    w.run(1200)
    assert w.rotables.exposure["rush_installs"] > 0
    ex = w.rotables.exposure
    assert 0.0 < ex["aog_days"] <= rot.AOG_CAP_DAYS * (ex["rush_installs"] + ex["purchases"]) + rot.FERRY_DAYS_PER_HOP * ex["ferry_hops"] + 1e-9


def test_counts_by_kind_nonnegative_and_pool_conserved(long_world: World) -> None:
    units = Counter(u.kind for u in long_world.rotables.units.values() if u.status != "scrapped")
    for k in long_world.kinds:
        stocked = sum(long_world.rotables.stock(k, 1e9).values())
        assert units[k] == long_world.counts[k] + stocked
    assert statistics.mean(long_world.rotables.slot_mult["avionics"]) > 0.5


def test_rotables_bands_within_lumped_medians() -> None:
    """Rotables add information, not change rates: per-kind median unscheduled counts (5 seeds, 24 tails, 1 year) stay
    within +/-25% (or 2 removals for small counts) of the lumped model."""
    acts = ("replace", "overhaul", "nff_return")
    seeds = (1, 7, 99, 1234, 4242)

    def counts(rotables: bool) -> dict[str, list[int]]:
        out: dict[str, list[int]] = defaultdict(list)
        for s in seeds:
            w = build_world({"seed": s, "fleet": 24, "rotables": rotables})
            w.run(2190)
            c = Counter(r.kind for r in w.removals() if r.action in acts and r.unscheduled)
            for k in w.kinds:
                out[k].append(c[k])
        return out

    off, on = counts(False), counts(True)
    for k in off:
        m_off, m_on = statistics.median(off[k]), statistics.median(on[k])
        assert abs(m_on - m_off) <= max(0.25 * m_off, 2.0), (k, off[k], on[k])
