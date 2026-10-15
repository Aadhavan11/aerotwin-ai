"""Stress / edge scenario suite on the v2 ``World`` (SIMULATED; expectations, not validation).

A ``Scenario`` is a perturbation of one deterministic world plus expected-direction assertions on metric deltas against a
baseline run with the SAME seed and tails (common random numbers: every random draw is keyed on (seed, stream, cycle), so
differences come from the perturbation, not from fresh noise). Nothing under ``app/`` is edited: scenarios are built on the
public surface (``World``, ``OperatingContext`` fed by ``build_context``, ``Fleet.maintain``, ``FaultInjector``, ``MelBook``).

Perturbation mechanisms
-----------------------
* flight profile transform (OAT, wet runway, density altitude): ``ScenarioWorld._profile`` rewrites the ``FlightProfile``.
* station environment (dust index, humidity): ``drivers.STATION_ENV`` is mutated for the duration of a run and restored
  (the only monkeypatch; scoped by ``_env_patched``, not thread-safe).
* utilisation: ``World(utilisation=u)``. ``LEGS_PER_DAY`` stays 6 legs/day at u=1, so the world cycle is always ONE flight leg
  and 365 * 6 = 2190 cycles = 365 days. At u=1.4 an aircraft flies 8.4 legs/day, i.e. a calendar year is 3066 cycles and each
  cycle advances the clock 1/1.4 as many days (fewer calendar-ageing days and inspection intervals per cycle). Per-cycle
  physics is untouched, so per-flight-hour rates must stay about the same while absolute counts scale with cycles.
* pre-ageing: ``live.age_world`` (cached ``World.pre_age``) to a target wear per kind.
* faults: ``World.inject_fault`` at ``INJECT_CYCLE`` (onsets may be back-dated before cycle 0, so any cycle works).
* part shortage: the sim has no stock model. A planned/unplanned replace or overhaul that would happen at a shortage
  station is held for ``delay_days`` (``MelBook`` category-A item with ``limit_days``), the unit keeps flying and the
  exposure (unit-cycles flown with a replacement pending, flown failed units) is counted.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterator, Sequence

import numpy as np

from app.sim.constants import ENGINE_FLIGHTS_PER_MODEL_CYCLE, ENGINE_CMAPSS_MEAN_LIFE_CYCLES
from app.sim.profile import FlightProfile, brake_energy_mj
from app.sim.v2 import drivers
from app.sim.v2.drivers import LEGS_PER_DAY
from app.sim.v2.live import age_world
from app.sim.v2.world import World

SCALED_TAILS = 8          # default-run fleet; the FULL fleet is 24 tails (marked ``slow`` in the tests)
FULL_TAILS = 24
YEAR_DAYS = 365.0
SAMPLE_EVERY = 50         # cycles between whole-fleet health samples
HOT_KINDS = ("avionics", "ecs", "electrical", "tires")
DEFER_KINDS_ACTIONS = ("replace", "overhaul")
# Wear fraction of a unit after +5 years of operation (ASSUMED priors, not calibrated): engines follow the live-service
# mapping cycles / mean on-wing life (capped at 0.6); consumables (brakes, tires) are replaced on wear so ageing them is
# meaningless; other kinds are given a fixed mid-life wear.
AGE_WEAR_OTHER = 0.35
AGE_WEAR_CAP = 0.6
INJECT_CYCLE = 100        # world cycle before which the fault scenarios inject (a back-dated onset is valid from cycle 0)


# ---------------------------------------------------------------- scenario plumbing
@dataclass
class Setup:
    """Mutable knobs a scenario perturbation edits."""

    profile_fns: list[Callable[[FlightProfile, str, int, int], FlightProfile]] = field(default_factory=list)
    dust_mult: float = 1.0
    humidity_add: float = 0.0
    utilisation: float = 1.0
    wear: dict[str, float] = field(default_factory=dict)          # kind -> uniform target wear (pre-ageing)
    events: list[tuple[int, Callable[[World], None]]] = field(default_factory=list)  # (cycle, fn(world)) before that cycle runs
    shortage_stations: tuple[str, ...] = ()
    delay_days: float = 0.0
    target_tail: int = 0      # tail whose coupled stress is traced


Check = Callable[["Metrics", "Metrics", dict[str, float]], str | None]  # (base, scen, deltas) -> failure message or None


@dataclass(frozen=True)
class Scenario:
    id: str
    description: str
    perturb: Callable[[Setup], None]
    expectations: Sequence[tuple[str, Check]] = ()   # (human text, check)
    expectation_text: str = ""


@dataclass
class Metrics:
    cycles: int
    flight_hours: float
    unsched_by_kind: dict[str, int]
    removals_by_kind: dict[str, int]   # every real (non-NFF) removal, planned or not
    nff_by_kind: dict[str, int]
    unsched_total: int            # unscheduled removals incl. NFF (MTBUR basis)
    mtbur_h: float
    nff_share: float
    engine_shop_visits: int
    planned_unplanned_ratio: float
    planned: int
    deferrals: int
    deferred_replacements: int
    deferral_exposure_cycles: int
    failed_in_service: int
    hi_p5: float
    hi_min: float
    stress_peak: dict[str, float]  # target tail, brakes / landing_gear
    stress_mean: dict[str, float]  # fleet-mean coupled stress_ext over the run
    stress_target_mean: dict[str, float]  # target-tail mean coupled stress_ext over the run (peak saturates at the clamps)
    engine_events: int
    engine_removals_target_tail: int
    engine_events_target: int      # detected inspections + actions on the target tail's engines after the first injection
    engine_faults_target: int      # ground-truth engine fault records on the target tail
    nan_free: bool
    invariants_ok: bool
    violations: list[str]
    rate_per_kfh: dict[str, float]

    def row(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("unsched_total", "mtbur_h", "nff_share", "engine_shop_visits", "planned_unplanned_ratio",
                                              "deferrals", "failed_in_service", "hi_p5", "hi_min")}


# ---------------------------------------------------------------- world subclass
class ScenarioWorld(World):
    """``World`` with a profile transform, scenario bookkeeping and station-shortage deferral of replacements."""

    def __init__(self, seed: int, tails: Sequence[str], setup: Setup) -> None:
        super().__init__(seed, tails, None, stochastic=False, utilisation=setup.utilisation)
        self.setup = setup
        self._arr: dict[str, str] = {}
        self._pending: dict[tuple[str, int], int] = {}      # (kind, unit) -> cycle at which the held work is executed
        self.deferred_replacements = 0
        self.exposure_cycles = 0
        self.failed_in_service = 0
        self.violations: list[str] = []

    def _profile(self, tail: str, cycle: int) -> tuple[FlightProfile, str]:
        p, arr = super()._profile(tail, cycle)
        self._arr[tail] = arr
        for fn in self.setup.profile_fns:
            p = fn(p, tail, cycle, self.seed)
        return p, arr

    def _do(self, kind: str, unit: int, action: str, reason: str, text: str, ctx: Any, induce: bool = False) -> None:
        key = (kind, unit)
        st = self.fleet.state[kind]
        if self.setup.shortage_stations and action in DEFER_KINDS_ACTIONS:
            tail = self.tails[int(self.unit_tail[kind][unit])]
            if key in self._pending:
                if self.fleet.cycle < self._pending[key]:
                    self.exposure_cycles += 1
                    return
                self._pending.pop(key)
            elif self._arr.get(tail) in self.setup.shortage_stations:
                t = int(self.unit_tail[kind][unit])
                self.maintenance.mel.defer(kind, unit, "A", self._tail_now(t), description=f"spare unavailable ({text})", tail=tail,
                                           limit_days=self.setup.delay_days)
                self._pending[key] = self.fleet.cycle + int(round(self.setup.delay_days * LEGS_PER_DAY * self.setup.utilisation))
                self.deferred_replacements += 1
                self.exposure_cycles += 1
                return
        if reason == "failure" and bool(st["failed"][unit]) and action != "nff_return":
            self.failed_in_service += 1
        super()._do(kind, unit, action, reason, text, ctx, induce)

    def _mel_expiry(self, ctx: Any) -> None:
        # held shortage items are released by their own delay (category A limit == delay), then closed by the real work
        for d in list(self.maintenance.mel.open_items()):
            if (d.kind, d.unit) in self._pending and d.description.startswith("spare unavailable"):
                continue
            t = self.tails.index(d.tail) if d.tail in self.tails else 0
            if d.expired(self._tail_now(t)):
                self._do(d.kind, d.unit, "repair", "deferred_defect_rectification", f"MEL {d.id} expired", ctx, induce=True)
                self.maintenance.mel.close(d.kind, d.unit, self._tail_now(t))


@contextlib.contextmanager
def _env_patched(dust_mult: float, humidity_add: float) -> Iterator[None]:
    saved = dict(drivers.STATION_ENV)
    try:
        for s, (h, d) in saved.items():
            drivers.STATION_ENV[s] = (min(100.0, h + humidity_add), min(1.0, d * dust_mult))
        yield
    finally:
        drivers.STATION_ENV.clear()
        drivers.STATION_ENV.update(saved)


# ---------------------------------------------------------------- perturbation building blocks
def _heat(dt: float) -> Callable[[FlightProfile, str, int, int], FlightProfile]:
    def f(p: FlightProfile, tail: str, cycle: int, seed: int) -> FlightProfile:
        return replace(p, oat_arr_c=p.oat_arr_c + dt, oat_dep_c=p.oat_dep_c + dt, density_alt_arr_ft=p.density_alt_arr_ft + 120.0 * dt)
    return f


def _set_oat(oat_c: float) -> Callable[[FlightProfile, str, int, int], FlightProfile]:
    def f(p: FlightProfile, tail: str, cycle: int, seed: int) -> FlightProfile:
        return replace(p, oat_arr_c=oat_c, oat_dep_c=oat_c, density_alt_arr_ft=p.density_alt_arr_ft + 120.0 * (oat_c - p.oat_arr_c))
    return f


def _wet(share: float) -> Callable[[FlightProfile, str, int, int], FlightProfile]:
    def f(p: FlightProfile, tail: str, cycle: int, seed: int) -> FlightProfile:
        u = int.from_bytes(hashlib.sha256(f"wet|{seed}|{tail}|{cycle}".encode()).digest()[:8], "big") / 2**64
        wet = u < share
        return replace(p, wet_runway=wet, brake_energy_mj=brake_energy_mj(p.landing_weight_frac, p.runway_len_ft, wet))
    return f


def _p_heat(s: Setup) -> None:
    s.profile_fns.append(_heat(15.0))


def _p_ageing(s: Setup) -> None:
    life = ENGINE_CMAPSS_MEAN_LIFE_CYCLES * ENGINE_FLIGHTS_PER_MODEL_CYCLE
    five_years_flights = 5.0 * YEAR_DAYS * LEGS_PER_DAY
    s.wear.update({"engine": min(five_years_flights / life, AGE_WEAR_CAP), "hydraulics": AGE_WEAR_OTHER, "apu": AGE_WEAR_OTHER,
                   "landing_gear": AGE_WEAR_OTHER, "avionics": AGE_WEAR_OTHER, "ecs": AGE_WEAR_OTHER, "electrical": AGE_WEAR_OTHER})


def _p_util(s: Setup) -> None:
    s.utilisation = 1.4


def _p_dusty(s: Setup) -> None:
    s.dust_mult, s.humidity_add = 2.0, 20.0


def _p_shortage(s: Setup) -> None:
    s.shortage_stations, s.delay_days = ("DEN", "MIA", "SFO"), 14.0


def _p_double_engine(s: Setup) -> None:
    def both(w: World) -> None:
        for u in (0, 1):   # units 0 and 1 are the two engines of tail 0
            w.inject_fault("engine", u, "hpt_blade_erosion", 1.0)
    s.events.append((INJECT_CYCLE, both))


def _p_hydraulics(s: Setup) -> None:
    # severity-1 pump_cavitation is itself the system failure now (efficiency loss drifts ~23x, hi < 0.3 within a few
    # hundred cycles); no latent state is raised by hand any more.
    s.events.append((INJECT_CYCLE, lambda w: w.inject_fault("hydraulics", 0, "pump_cavitation", 1.0)))


def _p_winter(s: Setup) -> None:
    s.profile_fns.extend([_set_oat(-10.0), _wet(0.6)])


def _p_combined(s: Setup) -> None:
    for f in (_p_heat, _p_ageing, _p_util, _p_dusty, _p_shortage, _p_double_engine, _p_hydraulics):
        f(s)
    s.profile_fns.append(_wet(0.6))


# ---------------------------------------------------------------- expectations (direction + rough size)
def _sum(m: Metrics, kinds: Sequence[str]) -> int:
    return sum(m.unsched_by_kind.get(k, 0) for k in kinds)


def _exp_heat(b: Metrics, s: Metrics, d: dict[str, float]) -> str | None:
    hb, hs = _sum(b, HOT_KINDS), _sum(s, HOT_KINDS)
    slack = 2.0 * math.sqrt(max(hb, 1))   # two Poisson sigmas: "equal" cannot be resolved more finely than this
    return None if hs >= hb - slack else f"hot-sensitive unscheduled removals fell {hb} -> {hs} (> 2 sigma)"


def _exp_age(b: Metrics, s: Metrics, d: dict[str, float]) -> str | None:
    kinds = ("engine", "hydraulics", "apu")
    return None if _sum(s, kinds) + s.engine_shop_visits >= _sum(b, kinds) + b.engine_shop_visits else "ageing did not raise engine/hydraulics/apu removals"


def _exp_util(b: Metrics, s: Metrics, d: dict[str, float]) -> str | None:
    if s.unsched_total < b.unsched_total:
        return f"absolute unscheduled removals fell {b.unsched_total} -> {s.unsched_total}"
    rb = b.unsched_total / b.flight_hours * 1000.0
    rs = s.unsched_total / s.flight_hours * 1000.0
    return None if abs(rs / rb - 1.0) <= 0.4 else f"per-1000FH rate moved {100 * (rs / rb - 1):+.0f}% (> +/-40%)"


def _exp_hyd(b: Metrics, s: Metrics, d: dict[str, float]) -> str | None:
    """Peak coupled stress is not lower than baseline (it saturates at the 1.5 / 1.4 clamps, and a natural cavitation onset on
    the target tail can already saturate the baseline) and the target tail's run-mean stress is higher."""
    out = []
    for k in ("brakes", "landing_gear"):
        pb, ps = b.stress_peak.get(k, 1.0), s.stress_peak.get(k, 1.0)
        mb, ms = b.stress_target_mean.get(k, 1.0), s.stress_target_mean.get(k, 1.0)
        if ps < pb - 1e-9 or ms <= mb + 1e-9:
            out.append(f"{k}: peak {pb:.3f}->{ps:.3f}, target-tail mean {mb:.3f}->{ms:.3f}")
    return None if not out else "coupled stress not raised: " + "; ".join(out)


def _exp_double(b: Metrics, s: Metrics, d: dict[str, float]) -> str | None:
    if s.engine_faults_target < 2:
        return f"only {s.engine_faults_target} engine fault records on the faulted tail"
    return None if s.engine_events_target >= 2 else f"only {s.engine_events_target} engine events on the faulted tail"


def _exp_shortage(b: Metrics, s: Metrics, d: dict[str, float]) -> str | None:
    if s.deferred_replacements <= 0:
        return "no replacement was deferred"
    return None if s.deferral_exposure_cycles > 0 else "no operational exposure recorded"


def _exp_wet(b: Metrics, s: Metrics, d: dict[str, float]) -> str | None:
    tb = b.removals_by_kind.get("brakes", 0) + b.removals_by_kind.get("tires", 0)
    ts = s.removals_by_kind.get("brakes", 0) + s.removals_by_kind.get("tires", 0)
    return None if ts >= tb - 2.0 * math.sqrt(max(tb, 1)) else f"brake+tire removals fell {tb} -> {ts} (> 2 sigma)"


def _exp_combined(b: Metrics, s: Metrics, d: dict[str, float]) -> str | None:
    return None if s.unsched_total >= b.unsched_total else "combined worst case produced fewer unscheduled removals than baseline"


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("heat_plus15", "OAT +15 C at every station, all year", _p_heat,
             (("avionics+ecs+electrical+tires unscheduled removals >= baseline", _exp_heat),),
             "hot-sensitive kinds rise or stay equal"),
    Scenario("ageing_plus5y", "fleet pre-aged by +5 years of utilisation (engines 0.6 wear cap, other kinds 0.35)", _p_ageing,
             (("engine/hydraulics/apu removals + engine shop visits >= baseline", _exp_age),),
             "engine / hydraulics / apu removals rise"),
    Scenario("utilisation_x1.4", "utilisation x1.4: 8.4 legs/day, a calendar year = 3066 cycles", _p_util,
             (("absolute unscheduled removals >= baseline and per-1000FH rate within +/-40%", _exp_util),),
             "absolute counts up, per-FH rate about constant"),
    Scenario("hot_dusty", "dust index x2, humidity +20 pp at every station", _p_dusty, (),
             "dust/humidity-driven faults (hpt erosion, hx fouling, fod cuts, connectors) more frequent"),
    Scenario("part_shortage", "no spares at DEN/MIA/SFO: replace/overhaul held 14 days when it falls due there", _p_shortage,
             (("replacements deferred and operational exposure recorded", _exp_shortage),),
             "deferrals and exposure appear, removal counts roughly unchanged"),
    Scenario("double_engine_fault", "hpt_blade_erosion severity 1 on both engines of tail 0 at cycle 100", _p_double_engine,
             (("faulted tail has 2 engine fault records and >= 2 engine events (detections or actions)", _exp_double),),
             ">= 2 engine events, world consistent"),
    Scenario("hydraulic_failure", "pump_cavitation severity 1 on tail 0 hydraulics at cycle 100", _p_hydraulics,
             (("coupled brakes and gear stress: peak not lower, target-tail mean above baseline", _exp_hyd),),
             "brake / gear stress coupling visible"),
    Scenario("wet_winter", "wet runway share 60%, arrival OAT -10 C", _p_winter,
             (("brake + tire removals not lower than baseline (2 sigma)", _exp_wet),), "brake energy / brake and tire wear up"),
    Scenario("combined_worst", "heat + ageing + x1.4 + hot/dusty + shortage + both faults + 60% wet", _p_combined,
             (("unscheduled removals >= baseline", _exp_combined),), "worse than any single scenario"),
)
BY_ID = {s.id: s for s in SCENARIOS}


# ---------------------------------------------------------------- run + metrics
def tails_for(n: int) -> list[str]:
    return [f"SIM-{i + 1:03d}" for i in range(n)]


def _build_wear(w: World, setup: Setup) -> dict[str, np.ndarray]:
    return {k: np.full(w.counts[k], float(setup.wear.get(k, 0.0))) for k in w.kinds}


def run_world(seed: int, n_tails: int, setup: Setup | None = None, days: float = YEAR_DAYS) -> tuple[ScenarioWorld, dict[str, Any]]:
    """Run one world for ``days`` calendar days at the setup's utilisation. Returns the world and a trace dict."""
    setup = setup or Setup()
    w = ScenarioWorld(seed, tails_for(n_tails), setup)
    if setup.wear:
        age_world(w, _build_wear(w, setup))
    cycles = int(round(days * LEGS_PER_DAY * setup.utilisation))
    events = sorted(setup.events, key=lambda e: e[0])
    ei = 0
    tt = setup.target_tail
    stress_peak = {"brakes": 1.0, "landing_gear": 1.0}
    stress_sum = {"brakes": 0.0, "landing_gear": 0.0}
    stress_tgt = {"brakes": 0.0, "landing_gear": 0.0}
    hi_samples: list[np.ndarray] = []
    with _env_patched(setup.dust_mult, setup.humidity_add):
        for c in range(cycles):
            while ei < len(events) and events[ei][0] <= w.cycle + 1:
                events[ei][1](w)
                ei += 1
            w.step()
            for k in stress_peak:
                if k in w.kinds:
                    s = np.asarray(w.fleet.state[k]["stress_ext"], dtype=float)
                    stress_peak[k] = max(stress_peak[k], float(s[tt]))
                    stress_sum[k] += float(s.mean())
                    stress_tgt[k] += float(s[tt])
            if (c + 1) % SAMPLE_EVERY == 0:
                hi_samples.append(np.concatenate([np.asarray(w.fleet.state[k]["hi"], dtype=float) for k in w.kinds]))
                _check_state(w, w.violations)
    return w, {"cycles": cycles, "stress_peak": stress_peak, "stress_mean": {k: v / max(cycles, 1) for k, v in stress_sum.items()},
               "stress_target_mean": {k: v / max(cycles, 1) for k, v in stress_tgt.items()},
               "hi": np.concatenate(hi_samples) if hi_samples else np.array([1.0])}


def _check_state(w: ScenarioWorld, out: list[str]) -> None:
    for k in w.kinds:
        for name, arr in w.fleet.state[k].items():
            a = np.asarray(arr)
            if a.dtype.kind == "f" and not np.isfinite(a).all():
                out.append(f"non-finite {k}.{name} at cycle {w.cycle}")
        hi = np.asarray(w.fleet.state[k]["hi"], dtype=float)
        if hi.min() < -1e-9 or hi.max() > 1.0 + 1e-9:
            out.append(f"hi out of [0,1] for {k} at cycle {w.cycle}: [{hi.min():.3f}, {hi.max():.3f}]")


def collect(w: ScenarioWorld, trace: dict[str, Any], setup: Setup) -> Metrics:
    rem = w.removals()
    ctx = w._ctx
    fh = float(np.sum(ctx.total_flight_hours)) if ctx is not None else 0.0
    kinds = list(w.kinds)
    unsched = {k: sum(1 for r in rem if r.kind == k and r.unscheduled and not r.nff) for k in kinds}
    nff = {k: sum(1 for r in rem if r.kind == k and r.nff) for k in kinds}
    allrem = {k: sum(1 for r in rem if r.kind == k and not r.nff) for k in kinds}
    unsched_total = sum(unsched.values()) + sum(nff.values())
    planned = sum(1 for r in rem if not r.unscheduled and not r.nff)
    tt = setup.target_tail
    tail_name = w.tails[tt]
    t_inj = min((c for c, _ in setup.events), default=0)
    violations = list(w.violations)
    # conservation checks
    n_events = sum(1 for e in w.events if e["action"] != "inspect")
    if n_events != len(rem):
        violations.append(f"events ({n_events}) != removals ({len(rem)})")
    for k in kinds:
        for i in range(w.counts[k]):
            if w.registry.on_unit(k, i) is None:
                violations.append(f"no part installed on {k}[{i}]")
    if ctx is not None and not (np.asarray(ctx.flight_cycles) == w.cycle).all():
        violations.append("flight_cycles != world cycle")
    if len({r.serial for r in rem if r.new_serial is None and r.action == "replace"}) != 0:
        violations.append("replace without new serial")
    hi = trace["hi"]
    return Metrics(
        cycles=trace["cycles"], flight_hours=fh, unsched_by_kind=unsched, removals_by_kind=allrem, nff_by_kind=nff, unsched_total=unsched_total,
        mtbur_h=fh / unsched_total if unsched_total else math.inf,
        nff_share=sum(nff.values()) / unsched_total if unsched_total else 0.0,
        engine_shop_visits=sum(1 for r in rem if r.kind == "engine" and r.action in ("overhaul", "replace")),
        planned_unplanned_ratio=planned / unsched_total if unsched_total else math.inf, planned=planned,
        deferrals=len(w.maintenance.mel.items), deferred_replacements=w.deferred_replacements,
        deferral_exposure_cycles=w.exposure_cycles, failed_in_service=w.failed_in_service,
        hi_p5=float(np.percentile(hi, 5)), hi_min=float(hi.min()),
        stress_peak=trace["stress_peak"], stress_mean=trace["stress_mean"], stress_target_mean=trace["stress_target_mean"],
        engine_events=sum(1 for e in w.events if e["kind"] == "engine"),
        engine_removals_target_tail=sum(1 for r in rem if r.kind == "engine" and r.tail == tail_name and not r.nff),
        engine_events_target=sum(1 for e in w.events if e["kind"] == "engine" and e["tail"] == tail_name and e["cycle"] >= t_inj
                                 and (e["action"] != "inspect" or e.get("detected"))),
        engine_faults_target=sum(1 for g in w.ground_truth() if g["kind"] == "engine" and g["unit"] // 2 == tt),
        nan_free=not any("non-finite" in v for v in violations), invariants_ok=not violations, violations=violations,
        rate_per_kfh={k: (unsched[k] / fh * 1000.0 if fh else 0.0) for k in kinds},
    )


def run_scenario(seed: int, n_tails: int, scenario: Scenario | None, days: float = YEAR_DAYS) -> Metrics:
    setup = Setup()
    if scenario is not None:
        scenario.perturb(setup)
    w, trace = run_world(seed, n_tails, setup, days)
    return collect(w, trace, setup)


@dataclass
class Outcome:
    scenario: Scenario
    metrics: Metrics
    deltas: dict[str, float]
    failures: list[str]


def deltas(base: Metrics, s: Metrics) -> dict[str, float]:
    d: dict[str, float] = {"unsched_total": s.unsched_total - base.unsched_total, "mtbur_h": s.mtbur_h - base.mtbur_h,
                           "nff_share": s.nff_share - base.nff_share, "engine_shop_visits": s.engine_shop_visits - base.engine_shop_visits,
                           "planned_unplanned_ratio": s.planned_unplanned_ratio - base.planned_unplanned_ratio,
                           "deferrals": s.deferrals - base.deferrals, "failed_in_service": s.failed_in_service - base.failed_in_service,
                           "hi_p5": s.hi_p5 - base.hi_p5, "hi_min": s.hi_min - base.hi_min}
    for k in s.unsched_by_kind:
        d[f"unsched.{k}"] = s.unsched_by_kind[k] - base.unsched_by_kind.get(k, 0)
    return d


def run_suite(seed: int = 1234, n_tails: int = SCALED_TAILS, scenarios: Sequence[Scenario] = SCENARIOS,
              base: Metrics | None = None) -> tuple[Metrics, list[Outcome]]:
    """Baseline once, then every scenario with the same seed / tails (common random numbers)."""
    base = base or run_scenario(seed, n_tails, None)
    out = []
    for sc in scenarios:
        m = run_scenario(seed, n_tails, sc)
        d = deltas(base, m)
        fails = [f"{text}: {msg}" for text, chk in sc.expectations if (msg := chk(base, m, d))]
        if not m.invariants_ok:
            fails.extend(m.violations[:3])
        out.append(Outcome(sc, m, d, fails))
    return base, out


__all__ = ["BY_ID", "Metrics", "Outcome", "SCENARIOS", "Scenario", "ScenarioWorld", "Setup", "collect", "deltas", "run_scenario", "run_suite",
           "run_world", "tails_for"]
