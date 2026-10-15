"""Detection back-test: inject each fault mode of ``app.sim.v2.faults.LIBRARY`` at a KNOWN onset into otherwise identical
worlds, stream the resulting telemetry through the real scorers cycle by cycle and measure how early / how often the
scorer's own alert (``HealthScore.is_anomaly``) fires.

CIRCULARITY CAVEAT (printed in every output): the same simulator generated the training data, the fault effects and
the test streams. Lead times and false-alarm rates therefore describe how the scorers behave on SIMULATED faults whose
signature the sim-trained model may have seen; they are not field performance.

Design
------
* One fault-free *control* world per seed (``faults=False``, ``auto_maintain=False``) and one *faulted* world per
  (seed, mode) with identical seed / tails / profiles in which every unit of the mode's kind gets the fault at
  ``onset + jitter`` (jitter is a deterministic per-unit offset, so the onset of each unit is known exactly).
* Functional failure = first cycle at or after onset where the unit is ``failed`` or its health index is at/below
  ``train_generic.EOL_HI`` (the same end-of-life definition the sim-trained model was trained on). A unit counts for
  lead time only if the control twin was not already at end of life by then (the failure is attributable to the fault).
* Scoring: every ``stride``-th cycle a window of the last 30 rows (``TELEMETRY_KEYS[kind]``, exactly what the service
  emits) goes through ``health.score_component``; scorer ``rule`` = default demo model, ``simtrained`` =
  ``HEALTH_MODEL=simtrained`` (needs artifacts), engines use the CMAPSS-trained model (``engine_model``, one scorer).
* Window labels: positive when the injected fault severity is >= ``FAULT_SEV_MIN``; negatives are faulted-unit
  windows before that point plus all control windows (before the control's own end of life).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional, Sequence

import numpy as np

SCHEMA = "aerotwin.backtest/1"
CAVEAT = ("CIRCULAR EVALUATION: the same simulator generated the training data, the injected faults and the test streams ('same "
          "simulator generated train and test'). Numbers describe behaviour on SIMULATED faults, not field performance.")
SCORERS_GENERIC = ("rule", "simtrained")
ENGINE_SCORER = "engine_model"
_T0 = datetime(2028, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class BacktestConfig:
    seeds: tuple[int, ...] = (11, 12)
    n_tails: int = 6
    onset: int = 150                  # base onset cycle (known)
    onset_jitter: int = 40            # unit i gets onset + (i * 7) % (jitter + 1)
    tail_cycles: int = 400            # simulate this long after the base onset plus the ramp (capped by max_horizon)
    max_horizon: int = 2200
    stride: int = 5                   # score every stride-th cycle
    modes: Optional[tuple[str, ...]] = None   # None: whole library
    scorers: Optional[tuple[str, ...]] = None  # None: rule (+ simtrained if artifacts exist)


def quick_config(modes: Sequence[str] = ("slow_leak", "stuck_brake")) -> BacktestConfig:
    return BacktestConfig(seeds=(11,), n_tails=3, onset=60, onset_jitter=20, tail_cycles=150, max_horizon=420, stride=10, modes=tuple(modes))


# --------------------------------------------------------------------------
# Pure metric functions (unit-tested on hand-built streams)
# --------------------------------------------------------------------------
def fh_at(fh_cum: np.ndarray, cycle: int) -> float:
    """Cumulative flight hours at the END of ``cycle`` (1-based); 0 before the first cycle."""
    if cycle <= 0:
        return 0.0
    return float(fh_cum[min(cycle, len(fh_cum)) - 1])


def unit_outcome(alert: np.ndarray, cycles: np.ndarray, onset: int, failure: Optional[int], fh_cum: np.ndarray) -> dict[str, Any]:
    """Detection outcome of one faulted unit. ``alert``/``cycles`` are the scored stream (bool, 1-based cycle numbers).

    The first alert at ``cycle >= onset`` (and ``<= failure`` when the unit fails) is the detection. delay = detection -
    onset; lead = failure - detection (cycles) and the flight hours between them. Alerts before onset are ignored here
    (they are counted as false alarms elsewhere). ``failure=None``: no functional failure in the horizon, lead is None.
    """
    alert = np.asarray(alert, dtype=bool)
    cycles = np.asarray(cycles)
    ok = alert & (cycles >= onset)
    if failure is not None:
        ok &= cycles <= failure
    out: dict[str, Any] = {"detected": bool(ok.any()), "delay_cycles": None, "lead_cycles": None, "lead_fh": None, "detect_cycle": None}
    if not ok.any():
        return out
    c = int(cycles[np.flatnonzero(ok)[0]])
    out["detect_cycle"] = c
    out["delay_cycles"] = c - int(onset)
    if failure is not None:
        out["lead_cycles"] = int(failure) - c
        out["lead_fh"] = fh_at(fh_cum, int(failure)) - fh_at(fh_cum, c)
    return out


def confusion(alert: np.ndarray, label: np.ndarray) -> dict[str, int]:
    a, y = np.asarray(alert, dtype=bool), np.asarray(label, dtype=bool)
    return {"tp": int((a & y).sum()), "fp": int((a & ~y).sum()), "fn": int((~a & y).sum()), "tn": int((~a & ~y).sum())}


def precision_recall(tp: int, fp: int, fn: int) -> dict[str, Optional[float]]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {"precision": p, "recall": r, "f1": f1}


def false_alarms(alert: np.ndarray, cycles: np.ndarray, fh_cum: np.ndarray) -> dict[str, float]:
    """Alert episodes (rising edges of the scored alert stream) and flight hours covered by the scored span."""
    a = np.asarray(alert, dtype=bool)
    cycles = np.asarray(cycles)
    if len(a) == 0:
        return {"episodes": 0, "fh": 0.0, "windows": 0, "alert_windows": 0}
    edges = int(a[0]) + int((a[1:] & ~a[:-1]).sum())
    fh = fh_at(fh_cum, int(cycles[-1])) - fh_at(fh_cum, int(cycles[0]) - 1)
    return {"episodes": edges, "fh": float(fh), "windows": int(len(a)), "alert_windows": int(a.sum())}


def false_alarms_per_1000fh(episodes: float, fh: float) -> Optional[float]:
    return None if fh <= 0 else 1000.0 * episodes / fh


def dist(x: Sequence[float]) -> dict[str, Any]:
    v = np.asarray([a for a in x if a is not None], dtype=float)
    if len(v) == 0:
        return {"n": 0, "p10": None, "p50": None, "p90": None}
    q = np.percentile(v, [10, 50, 90])
    return {"n": int(len(v)), "p10": float(q[0]), "p50": float(q[1]), "p90": float(q[2])}


# --------------------------------------------------------------------------
# Raw accumulators -> summary
# --------------------------------------------------------------------------
def new_acc() -> dict[str, Any]:
    return {"n_units": 0, "n_failed": 0, "n_detected": 0, "n_detected_failed": 0, "delay": [], "lead_c": [], "lead_fh": [],
            "tp": 0, "fp": 0, "fn": 0, "tn": 0, "ctrl_episodes": 0, "ctrl_fh": 0.0, "ctrl_windows": 0, "ctrl_alert_windows": 0,
            "n_ctrl_units": 0, "pre_onset_alerts": 0, "fallbacks": 0, "scored": 0}


def merge_acc(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    for k, v in b.items():
        a[k] = a[k] + v
    return a


def summarise(acc: dict[str, Any]) -> dict[str, Any]:
    pr = precision_recall(acc["tp"], acc["fp"], acc["fn"])
    return {
        "n_units": acc["n_units"], "n_units_failed": acc["n_failed"], "n_detected": acc["n_detected"],
        "unit_recall": (acc["n_detected"] / acc["n_units"]) if acc["n_units"] else None,
        "recall_before_failure": (acc["n_detected_failed"] / acc["n_failed"]) if acc["n_failed"] else None,
        "detection_delay_cycles": dist(acc["delay"]), "lead_time_cycles": dist(acc["lead_c"]), "lead_time_fh": dist(acc["lead_fh"]),
        "window_precision": pr["precision"], "window_recall": pr["recall"], "window_f1": pr["f1"],
        "confusion": {k: acc[k] for k in ("tp", "fp", "fn", "tn")},
        "false_alarms_per_1000fh": false_alarms_per_1000fh(acc["ctrl_episodes"], acc["ctrl_fh"]),
        "control": {"units": acc["n_ctrl_units"], "flight_hours": acc["ctrl_fh"], "alert_episodes": acc["ctrl_episodes"],
                    "windows": acc["ctrl_windows"], "alert_window_fraction": (acc["ctrl_alert_windows"] / acc["ctrl_windows"]) if acc["ctrl_windows"] else None},
        "pre_onset_alerts_in_faulted_units": acc["pre_onset_alerts"], "scorer_fallbacks": acc["fallbacks"], "windows_scored": acc["scored"],
    }


# --------------------------------------------------------------------------
# Simulation and scoring
# --------------------------------------------------------------------------
class _Run:
    """Telemetry rows, cumulative flight hours and end-of-life cycle per unit of one simulated world."""

    def __init__(self, seed: int, n_tails: int, horizon: int, kinds: Sequence[str], onsets: Optional[dict[tuple[str, str], np.ndarray]] = None) -> None:
        from app.ml import train_generic as tg
        from app.ml.sensors import TELEMETRY_KEYS
        from app.sim.v2.world import World

        tails = [f"BT{seed % 100:02d}-{i:02d}" for i in range(n_tails)]
        w = World(seed, tails, stochastic=True, auto_maintain=False, faults=False)
        if onsets:
            for key, arr in onsets.items():
                w.injector.onset[key][:] = arr
        self.counts = {k: w.counts[k] for k in kinds}
        self.rows: dict[tuple[str, int], list[dict[str, Any]]] = {(k, i): [] for k in kinds for i in range(w.counts[k])}
        self.fh = {k: np.zeros((horizon, w.counts[k])) for k in kinds}
        self.eol = {k: np.zeros((horizon, w.counts[k]), dtype=bool) for k in kinds}
        self.tails = {(k, i): tails[int(w.unit_tail[k][i])] for k in kinds for i in range(w.counts[k])}
        index = {w.component_id(k, i): (k, i) for k in kinds for i in range(w.counts[k])}
        for c in range(horizon):
            rows = w.step()
            w.rows.clear()
            for r in rows:
                ki = index.get(r["component_id"])
                if ki is not None:
                    keep = {ch: v for ch, v in r["values"].items() if ch in TELEMETRY_KEYS[ki[0]]}
                    self.rows[ki].append({"cid": r["component_id"], "cycle": r["cycle"], "values": keep})
            hi = w.fleet.health_index()
            for k in kinds:
                self.fh[k][c] = np.asarray(w.fleet._ctx_for(k, w._ctx).flight_hours, dtype=float)
                self.eol[k][c] = np.asarray(w.fleet.state[k]["failed"], dtype=bool) | (np.asarray(hi[k]) <= tg.EOL_HI)
        for k in kinds:
            self.fh[k] = np.cumsum(self.fh[k], axis=0)

    def first_eol(self, kind: str, unit: int, start: int = 1) -> Optional[int]:
        e = np.flatnonzero(self.eol[kind][start - 1:, unit])
        return None if len(e) == 0 else int(e[0]) + start


@contextmanager
def _health_model(flag: str) -> Iterator[None]:
    old = os.environ.get("HEALTH_MODEL")
    os.environ["HEALTH_MODEL"] = flag
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("HEALTH_MODEL", None)
        else:
            os.environ["HEALTH_MODEL"] = old


def _score_stream(kind: str, rows: list[dict[str, Any]], upto: int, stride: int, scorer: str) -> tuple[np.ndarray, np.ndarray, int]:
    """Alert stream of one unit for cycles 3..upto at ``stride``. Returns (cycles, alert bool, n_fallbacks)."""
    from app.contracts.models import TelemetryRow
    from app.ml import health

    flag = "simtrained" if scorer == "simtrained" else "rule"
    cyc, al, fb = [], [], 0
    with _health_model(flag):
        for c in range(3, min(upto, len(rows)) + 1):
            if (c - 3) % stride:
                continue
            win = [TelemetryRow(tail="BT", component_id=r["cid"], ts=_T0 + timedelta(hours=r["cycle"]), cycle=int(r["cycle"]), values=r["values"])
                   for r in rows[max(0, c - health.WINDOW):c]]
            s = health.score_component(kind, "BT", rows[0]["cid"], win)
            cyc.append(c)
            al.append(bool(s.is_anomaly))
            if flag == "simtrained" and s.model_version != health.SIMTRAINED_VERSION:
                fb += 1
    return np.asarray(cyc, dtype=int), np.asarray(al, dtype=bool), fb


def available_scorers(kind: str) -> list[str]:
    from app.ml import train_generic as tg

    if kind == "engine":
        return [ENGINE_SCORER]
    out = ["rule"]
    if tg.load(kind) is not None:
        out.append("simtrained")
    return out


def run_backtest(cfg: Optional[BacktestConfig] = None, log_fn=None) -> dict[str, Any]:
    """Inject every selected mode and report per mode / per kind / per scorer. Deterministic given ``cfg``."""
    from app.ml import train_generic as tg
    from app.sim.v2.faults import LIBRARY

    cfg = cfg or BacktestConfig()
    t0 = time.time()
    modes = [LIBRARY[m] for m in (cfg.modes or tuple(LIBRARY))]
    kinds = sorted({m.kind for m in modes})
    horizon_of = {m.id: int(min(cfg.max_horizon, cfg.onset + cfg.onset_jitter + m.ramp_cycles + cfg.tail_cycles)) for m in modes}
    t_max = max(horizon_of.values())
    per_mode: dict[str, dict[str, dict[str, Any]]] = {}
    for seed in cfg.seeds:
        ctrl = _Run(seed, cfg.n_tails, t_max, kinds)
        if log_fn:
            log_fn(f"seed {seed}: control world done ({time.time() - t0:.0f}s)")
        for m in modes:
            h = horizon_of[m.id]
            n = ctrl.counts[m.kind]
            onset_u = cfg.onset + (np.arange(n) * 7) % (cfg.onset_jitter + 1)
            onsets = {(m.kind, m.id): onset_u.astype(np.int64)}
            run = _Run(seed, cfg.n_tails, h, [m.kind], onsets)
            scorers = [s for s in available_scorers(m.kind) if cfg.scorers is None or s in cfg.scorers or (m.kind == "engine")]
            for sc in scorers:
                acc = per_mode.setdefault(m.id, {}).setdefault(sc, new_acc())
                for u in range(n):
                    on = int(onset_u[u])
                    fail = run.first_eol(m.kind, u, on)
                    cfail = ctrl.first_eol(m.kind, u, 1)
                    attributable = fail is not None and (cfail is None or cfail > fail)
                    upto = fail if fail is not None else h
                    cyc, al, fb = _score_stream(m.kind, run.rows[(m.kind, u)], upto, cfg.stride, sc)
                    fh = run.fh[m.kind][:, u]
                    sev = np.asarray(m.severity(np.maximum(cyc - on, 0)), dtype=float)
                    label = (cyc >= on) & (sev >= tg.FAULT_SEV_MIN)
                    # a unit whose control twin was already at end of life before the fault's own failure is not attributable
                    cf_ok = cfail is None or cfail > on
                    keep = np.ones(len(cyc), dtype=bool) if cf_ok else np.zeros(len(cyc), dtype=bool)
                    cm = confusion(al[keep], label[keep])
                    acc["n_units"] += 1
                    acc["scored"] += int(len(cyc))
                    acc["fallbacks"] += fb
                    acc["pre_onset_alerts"] += int((al & (cyc < on)).sum())
                    for k_ in ("tp", "fp", "fn", "tn"):
                        acc[k_] += cm[k_]
                    o = unit_outcome(al, cyc, on, fail if attributable else None, fh)
                    acc["n_detected"] += int(o["detected"])
                    if attributable:
                        acc["n_failed"] += 1
                        acc["n_detected_failed"] += int(o["detected"])
                    if o["delay_cycles"] is not None:
                        acc["delay"].append(o["delay_cycles"])
                    if o["lead_cycles"] is not None:
                        acc["lead_c"].append(o["lead_cycles"])
                        acc["lead_fh"].append(o["lead_fh"])
                    # matched fault-free twin: same seed, same unit, same scorer, up to its own end of life
                    cupto = min(h, cfail) if cfail is not None else h
                    cc, ca, cfb = _score_stream(m.kind, ctrl.rows[(m.kind, u)], cupto, cfg.stride, sc)
                    fa = false_alarms(ca, cc, ctrl.fh[m.kind][:, u])
                    acc["n_ctrl_units"] += 1
                    acc["ctrl_episodes"] += fa["episodes"]
                    acc["ctrl_fh"] += fa["fh"]
                    acc["ctrl_windows"] += fa["windows"]
                    acc["ctrl_alert_windows"] += fa["alert_windows"]
                    cm_ctrl = confusion(ca, np.zeros(len(ca), dtype=bool))
                    acc["fp"] += cm_ctrl["fp"]
                    acc["tn"] += cm_ctrl["tn"]
            if log_fn:
                log_fn(f"{seed}/{m.id}: done ({time.time() - t0:.0f}s)")
    per_kind: dict[str, dict[str, dict[str, Any]]] = {}
    for m in modes:
        for sc, acc in per_mode.get(m.id, {}).items():
            tgt = per_kind.setdefault(m.kind, {}).setdefault(sc, new_acc())
            merge_acc(tgt, {k: (list(v) if isinstance(v, list) else v) for k, v in acc.items()})
    return {
        "schema": SCHEMA, "caveat": CAVEAT, "config": asdict(cfg), "provenance": "SIMULATED",
        "definitions": {
            "alert": "HealthScore.is_anomaly of the scorer (rule: anomaly_score>=0.75; simtrained: classifier prob >= calibrated threshold)",
            "functional_failure": f"first cycle >= onset with failed flag or health index <= {tg.EOL_HI} (train_generic.EOL_HI)",
            "detection_delay": "first alert at/after onset minus onset, cycles",
            "lead_time": "functional failure cycle minus first alert cycle (cycles, and flight hours); units that never fail excluded",
            "window_label": f"positive when injected severity >= {tg.FAULT_SEV_MIN}; negatives: pre-fault + matched control windows",
            "false_alarms_per_1000fh": "alert episodes (rising edges) on fault-free matched units per 1000 flight hours, until the control's own end of life",
            "quantisation": f"alerts are evaluated every {cfg.stride} cycles, so lead time and delay are quantised to that step",
        },
        "modes": {mid: {"kind": LIBRARY[mid].kind, "onset_cycle": cfg.onset, "ramp_cycles": LIBRARY[mid].ramp_cycles, "shape": LIBRARY[mid].shape,
                        "scorers": {sc: summarise(a) for sc, a in sc_acc.items()}} for mid, sc_acc in per_mode.items()},
        "kinds": {k: {sc: summarise(a) for sc, a in d.items()} for k, d in per_kind.items()},
        "seconds": round(time.time() - t0, 1),
    }


def to_markdown(res: dict[str, Any]) -> str:
    def f(x: Any, d: int = 1) -> str:
        return "-" if x is None else f"{x:.{d}f}"

    def head(first: str) -> list[str]:
        return [f"| {first} | scorer | units | failed | recall (before failure) | lead p10/p50/p90 (cycles) | lead p50 (FH) | delay p50 (cyc) "
                "| precision | recall (windows) | FA / 1000 FH |", "|---|---|---|---|---|---|---|---|---|---|---|"]

    def row(label: str, sc: str, s: dict[str, Any]) -> str:
        lc, lf, dl = s["lead_time_cycles"], s["lead_time_fh"], s["detection_delay_cycles"]
        return (f"| {label} | {sc} | {s['n_units']} | {s['n_units_failed']} | {f(s['recall_before_failure'], 2)} | {f(lc['p10'], 0)}/{f(lc['p50'], 0)}/{f(lc['p90'], 0)} "
                f"| {f(lf['p50'])} | {f(dl['p50'], 0)} | {f(s['window_precision'], 2)} | {f(s['window_recall'], 2)} | {f(s['false_alarms_per_1000fh'], 2)} |")

    lines = [f"> {res['caveat']}", "", "### Per kind (pooled over fault modes)", "", *head("kind")]
    for k, d in sorted(res["kinds"].items()):
        lines += [row(k, sc, s) for sc, s in d.items()]
    lines += ["", "### Per fault mode", "", *head("mode")]
    for mid, d in sorted(res["modes"].items(), key=lambda kv: (kv[1]["kind"], kv[0])):
        lines += [row(f"{mid} ({d['kind']})", sc, s) for sc, s in d["scorers"].items()]
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Detection back-test on injected simulated faults")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default="out/ml-validity/backtest.json")
    a = ap.parse_args(argv)
    res = run_backtest(quick_config() if a.quick else BacktestConfig(), log_fn=print)
    from pathlib import Path

    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=1, default=float))
    p.with_suffix(".md").write_text(to_markdown(res))
    print(to_markdown(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
