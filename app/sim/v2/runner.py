"""Background backfill runner for the v2 world (``/sim/runs``).

A run simulates ``years`` of fleet operation (nominally 6 legs/day per tail, so 2,190 world cycles per year) as fast as
the CPU allows ("time compression") on a worker thread that is independent of the request path, and writes
``data/sim_runs/<id>/``:

* ``parts/telemetry_<cycle>.csv.gz``: one gzip member per checkpoint segment (header in the first); the concatenation of
  the parts IS the export (``cat`` of gzip members is a valid gzip file)
* ``removals.csv.gz``, ``ground_truth.json`` (hidden fault truth), ``events.json`` (maintenance), ``manifest.json``
* ``ckpt_<cycle>.npz``: full world snapshot at each segment boundary (``sim_checkpoints`` rows point at them)

Everything carries provenance ``SIMULATED``. Resume loads the latest checkpoint, deletes later partial parts and continues;
the world is counter-RNG deterministic, so a resumed run is bit-identical to an uninterrupted one.
Tables ``sim_runs`` and ``sim_checkpoints`` are created with ``CREATE TABLE IF NOT EXISTS`` (additive only).
"""

from __future__ import annotations

import csv
import dataclasses
import gzip
import io
import json
import os
import queue
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np

from app import settings
from app.sim.v2.live import age_world, initial_wear
from app.sim.v2.world import PROVENANCE, World

LEGS_PER_DAY = 6.0
BASE_LEGS_PER_DAY = 3.0  # drivers.BASE_CYCLES_PER_DAY at utilisation 1.0
CYCLES_PER_YEAR = int(round(365 * LEGS_PER_DAY))
DEFAULT_SEGMENT = 365  # cycles (~2 months) between checkpoints
RUNS_DIR = Path(os.environ.get("SIM_RUNS_DIR", str(settings.ROOT / "data" / "sim_runs")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_runs(
  id TEXT PRIMARY KEY, status TEXT NOT NULL, years REAL, seed INTEGER, spec_json TEXT, total_cycles INTEGER,
  cycles_done INTEGER DEFAULT 0, created REAL, started REAL, finished REAL, session_started REAL, session_c0 INTEGER DEFAULT 0,
  error TEXT, out_dir TEXT, provenance TEXT DEFAULT 'SIMULATED', rows INTEGER DEFAULT 0, state_hash TEXT);
CREATE TABLE IF NOT EXISTS sim_checkpoints(
  run_id TEXT, cycle INTEGER, path TEXT, state_hash TEXT, created REAL, PRIMARY KEY(run_id, cycle));
"""
RESUMABLE = ("queued", "interrupted", "failed", "running")  # "running" without a live worker = the process died


class SimInterrupted(Exception):
    """Raised inside a run to stop it at the next checkpoint (tests / graceful stop)."""


def _channels() -> list[str]:
    from app.sim.v2 import core
    out: list[str] = []
    for k in core.kinds():
        for c in core.get(k).channels:
            if c not in out:
                out.append(c)
    return sorted(out)


def build_world(spec: dict[str, Any]) -> World:
    """The deterministic world for a run spec ``{seed, fleet, initial_wear, ...}``."""
    from app.sim.fleet import generate_fleet
    seed = int(spec["seed"])
    state = generate_fleet(seed)
    all_tails = [a.tail for a in state.aircraft]
    fleet = spec.get("fleet")
    if fleet is None:
        tails = all_tails
    elif isinstance(fleet, int):
        tails = [f"SIM-{i:03d}" for i in range(1, fleet + 1)]
    else:
        tails = list(fleet)
    w = World(seed, tails, stochastic=True, utilisation=LEGS_PER_DAY / BASE_LEGS_PER_DAY, auto_maintain=True)
    if spec.get("initial_wear", True):
        age_world(w, initial_wear(state, w))
    return w


class Runner:
    def __init__(self, db_path: str | Path | None = None, out_dir: str | Path | None = None) -> None:
        self.db_path = str(db_path or settings.DB_PATH)
        self.out_dir = Path(out_dir) if out_dir else RUNS_DIR
        self._q: "queue.Queue[str]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._live: set[str] = set()
        self._lock = threading.Lock()
        self._ready = False

    # ---- storage ----
    def _db(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        if not self._ready:
            c.executescript(SCHEMA)
            self._ready = True
        return c

    def _update(self, run_id: str, **cols: Any) -> None:
        keys = ", ".join(f"{k}=?" for k in cols)
        with self._db() as c:
            c.execute(f"UPDATE sim_runs SET {keys} WHERE id=?", (*cols.values(), run_id))

    def _row(self, run_id: str) -> sqlite3.Row:
        with self._db() as c:
            r = c.execute("SELECT * FROM sim_runs WHERE id=?", (run_id,)).fetchone()
        if r is None:
            raise KeyError(run_id)
        return r

    # ---- public API ----
    def create(self, years: float, seed: int = 1234, fleet: Any = None, initial_wear_on: bool = True,
               checkpoint_every: int = DEFAULT_SEGMENT, start: bool = True) -> str:
        if not 0 < years <= 20:
            raise ValueError("years must be in (0, 20]")
        if checkpoint_every < 1:
            raise ValueError("checkpoint_every must be >= 1")
        run_id = uuid.uuid4().hex[:12]
        spec = {"seed": int(seed), "fleet": fleet, "initial_wear": bool(initial_wear_on), "checkpoint_every": int(checkpoint_every),
                "legs_per_day": LEGS_PER_DAY}
        total = max(1, int(round(years * CYCLES_PER_YEAR)))
        with self._db() as c:
            c.execute("INSERT INTO sim_runs(id,status,years,seed,spec_json,total_cycles,created,out_dir) VALUES(?,?,?,?,?,?,?,?)",
                      (run_id, "queued", float(years), int(seed), json.dumps(spec), total, time.time(), str(self.out_dir / run_id)))
        if start:
            self.submit(run_id)
        return run_id

    def submit(self, run_id: str) -> None:
        with self._lock:
            if run_id in self._live:
                return
            self._live.add(run_id)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._worker, name="sim-runner", daemon=True)
                self._thread.start()
        self._q.put(run_id)

    def resume(self, run_id: str) -> dict[str, Any]:
        r = self._row(run_id)
        if r["status"] == "done":
            return self.status(run_id)
        if r["status"] not in RESUMABLE:
            raise ValueError(f"run is {r['status']}")
        if run_id in self._live:
            raise ValueError("run is already active")
        self._update(run_id, status="queued", error=None)
        self.submit(run_id)
        return self.status(run_id)

    def status(self, run_id: str) -> dict[str, Any]:
        r = self._row(run_id)
        status = r["status"]
        if status == "running" and run_id not in self._live:
            status = "interrupted"
        done, total = int(r["cycles_done"]), int(r["total_cycles"])
        eta = None
        if status == "running" and r["session_started"]:
            gained = done - int(r["session_c0"] or 0)
            if gained > 0:
                eta = round((time.time() - r["session_started"]) / gained * (total - done), 1)
        elif status == "done":
            eta = 0.0
        return {"id": run_id, "status": status, "provenance": PROVENANCE, "years": r["years"], "seed": r["seed"],
                "cycles_done": done, "total_cycles": total, "progress": round(done / total, 4) if total else 1.0,
                "eta_seconds": eta, "rows": int(r["rows"] or 0), "error": r["error"], "state_hash": r["state_hash"],
                "spec": json.loads(r["spec_json"])}

    def list(self) -> list[dict[str, Any]]:
        with self._db() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM sim_runs ORDER BY created DESC")]
        return [self.status(i) for i in ids]

    def export_path(self, run_id: str) -> Path:
        """Telemetry export: parquet when pyarrow/fastparquet is importable, else the concatenated csv.gz."""
        r = self._row(run_id)
        if r["status"] != "done":
            raise ValueError("run is not finished")
        out = Path(r["out_dir"])
        gz = out / "telemetry.csv.gz"
        if not gz.exists():
            with open(gz, "wb") as dst:
                for part in sorted((out / "parts").glob("telemetry_*.csv.gz")):
                    with open(part, "rb") as src:
                        shutil.copyfileobj(src, dst)
        from importlib.util import find_spec
        if find_spec("pyarrow") or find_spec("fastparquet"):
            pq = out / "telemetry.parquet"
            if not pq.exists():
                import pandas as pd
                pd.read_csv(gz).to_parquet(pq, index=False)
            return pq
        return gz

    # ---- worker ----
    def _worker(self) -> None:
        while True:
            rid = self._q.get()
            try:
                self.execute(rid)
            finally:
                with self._lock:
                    self._live.discard(rid)

    def execute(self, run_id: str, stop_after_cycle: int | None = None, crash_at_cycle: int | None = None,
                on_cycle: Callable[[int], None] | None = None) -> dict[str, Any]:
        """Run (or continue) ``run_id`` synchronously. ``stop_after_cycle`` stops cleanly at the first checkpoint at/after it
        (status interrupted); ``crash_at_cycle`` raises mid-segment (status failed, partial part left behind)."""
        r = self._row(run_id)
        spec, total = json.loads(r["spec_json"]), int(r["total_cycles"])
        out = Path(r["out_dir"])
        parts = out / "parts"
        parts.mkdir(parents=True, exist_ok=True)
        try:
            w = build_world(spec)
            with self._db() as c:
                ck = c.execute("SELECT cycle, path FROM sim_checkpoints WHERE run_id=? ORDER BY cycle DESC LIMIT 1", (run_id,)).fetchone()
            c0, rows_done = 0, 0
            if ck is not None and Path(ck["path"]).exists():
                with np.load(ck["path"], allow_pickle=False) as z:
                    w.restore({k.replace("__", "/"): z[k] for k in z.files})
                c0 = int(ck["cycle"])
                rows_done = int(json.loads((out / "manifest.partial.json").read_text())["rows"]) if (out / "manifest.partial.json").exists() else 0
            for stale in parts.glob("telemetry_*"):
                if int(stale.name.split("_")[1].split(".")[0]) >= c0:
                    stale.unlink()
            with self._db() as c:
                c.execute("DELETE FROM sim_checkpoints WHERE run_id=? AND cycle>?", (run_id, c0))
            now = time.time()
            self._update(run_id, status="running", started=r["started"] or now, session_started=now, session_c0=c0,
                         cycles_done=c0, rows=rows_done, error=None)
            chans = _channels()
            seg = int(spec.get("checkpoint_every", DEFAULT_SEGMENT))
            kind_of = {w.component_id(k, i): k for k in w.kinds for i in range(w.counts[k])}
            while c0 < total:
                n = min(seg, total - c0)
                tmp = parts / f"telemetry_{c0:08d}.csv.gz.tmp"
                with gzip.GzipFile(str(tmp), "wb", compresslevel=1, mtime=0) as gz:
                    text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
                    wr = csv.writer(text, lineterminator="\n")
                    if c0 == 0:
                        wr.writerow(["tail", "component_id", "kind", "cycle", "ts", "provenance", *chans])
                    for j in range(n):
                        cyc = c0 + j + 1
                        if crash_at_cycle is not None and cyc == crash_at_cycle:
                            text.flush()
                            raise RuntimeError(f"simulated crash at cycle {cyc}")
                        for row in w.step():
                            v = row["values"]
                            wr.writerow([row["tail"], row["component_id"], kind_of[row["component_id"]], row["cycle"], row["ts"],
                                         PROVENANCE, *[v.get(c, "") for c in chans]])
                            rows_done += 1
                        w.rows = []
                        if on_cycle is not None:
                            on_cycle(cyc)
                    text.flush()
                    text.detach()
                os.replace(tmp, parts / f"telemetry_{c0:08d}.csv.gz")
                c0 += n
                self._checkpoint(run_id, out, w, c0, rows_done)
                if stop_after_cycle is not None and c0 >= stop_after_cycle and c0 < total:
                    raise SimInterrupted()
            self._finish(run_id, out, w, spec, total, rows_done)
        except SimInterrupted:
            self._update(run_id, status="interrupted")
        except Exception as exc:  # noqa: BLE001  (surfaced in the run status, never silently dropped)
            self._update(run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        return self.status(run_id)

    def _checkpoint(self, run_id: str, out: Path, w: World, cycle: int, rows: int) -> None:
        path = out / f"ckpt_{cycle:08d}.npz"
        tmp = out / f"ckpt_{cycle:08d}.tmp.npz"
        np.savez_compressed(tmp, **{k.replace("/", "__"): v for k, v in w.snapshot().items()})
        os.replace(tmp, path)
        (out / "manifest.partial.json").write_text(json.dumps({"rows": rows, "cycle": cycle}))
        with self._db() as c:
            c.execute("INSERT OR REPLACE INTO sim_checkpoints VALUES(?,?,?,?,?)", (run_id, cycle, str(path), w.state_hash(), time.time()))
            c.execute("UPDATE sim_runs SET cycles_done=?, rows=? WHERE id=?", (cycle, rows, run_id))

    def _finish(self, run_id: str, out: Path, w: World, spec: dict[str, Any], total: int, rows: int) -> None:
        recs = [dataclasses.asdict(r) for r in w.removals()]
        buf = io.StringIO()
        if recs:
            wr = csv.DictWriter(buf, fieldnames=list(recs[0]), lineterminator="\n")
            wr.writeheader()
            wr.writerows(recs)
        with gzip.GzipFile(str(out / "removals.csv.gz"), "wb", compresslevel=6, mtime=0) as g:
            g.write(buf.getvalue().encode())
        (out / "ground_truth.json").write_text(json.dumps({"provenance": PROVENANCE, "faults": w.ground_truth()}, sort_keys=True))
        (out / "events.json").write_text(json.dumps({"provenance": PROVENANCE, "events": w.events}, sort_keys=True))
        h = w.state_hash()
        (out / "manifest.json").write_text(json.dumps({
            "provenance": PROVENANCE, "run_id": run_id, "spec": spec, "cycles": total, "rows": rows, "removals": len(recs),
            "state_hash": h, "tails": w.tails, "note": "SIMULATED data; ground_truth.json is hidden truth, never telemetry"}, sort_keys=True))
        (out / "telemetry.csv.gz").unlink(missing_ok=True)
        (out / "telemetry.parquet").unlink(missing_ok=True)
        self._update(run_id, status="done", finished=time.time(), cycles_done=total, rows=rows, state_hash=h)


_RUNNER: Runner | None = None


def get_runner() -> Runner:
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = Runner()
    return _RUNNER
