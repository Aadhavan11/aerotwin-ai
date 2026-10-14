"""Simulator v2 core: component-model protocol, counter-based RNG, registry and fleet container.

Determinism contract: every random draw for (kind, purpose, cycle) comes from ``StreamRng.at(cycle)``,
a fresh Philox generator keyed on (seed, hash(stream name), unit block) with the cycle in the counter.
State at cycle N therefore depends only on the seed, config and the sequence of maintenance actions,
never on how steps were batched or whether the run was snapshotted/restored in between.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

import numpy as np

if TYPE_CHECKING:
    from app.sim.v2.drivers import OperatingContext

State = dict[str, np.ndarray]
ACTIONS = ("inspect", "repair", "overhaul", "replace")


def _stream_hash(name: str) -> int:
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "little")


class StreamRng:
    """Counter-based RNG: ``at(cycle)`` returns the same Generator state for the same (seed, stream, block, cycle)."""

    def __init__(self, seed: int, stream: str, block: int = 0) -> None:
        self.seed = int(seed)
        self.stream = stream
        self.block = int(block)
        self._key = (self.seed & (2**64 - 1), _stream_hash(f"{stream}|{self.block}"))

    def at(self, cycle: int) -> np.random.Generator:
        # Philox counter is 4 x uint64; the cycle sits in the top word so word-0 increments never collide.
        counter = [0, 0, 0, int(cycle) & (2**64 - 1)]
        return np.random.Generator(np.random.Philox(key=list(self._key), counter=counter))


@runtime_checkable
class ComponentModel(Protocol):
    kind: str
    channels: tuple[str, ...]

    def init(self, n: int, rng: np.random.Generator) -> State: ...

    def step(self, state: State, ctx: "OperatingContext", rng: np.random.Generator) -> None: ...

    def emit(self, state: State, ctx: "OperatingContext", rng: np.random.Generator) -> dict[str, np.ndarray]: ...

    def health_index(self, state: State) -> np.ndarray: ...

    def maintain(self, state: State, idx: np.ndarray, action: str, q: float, rng: np.random.Generator) -> None: ...


class Registry:
    """Maps component kind -> model factory (class)."""

    def __init__(self) -> None:
        self._models: dict[str, Callable[[], ComponentModel]] = {}

    def register(self, kind: str) -> Callable[[Any], Any]:
        def deco(cls: Any) -> Any:
            if kind in self._models:
                raise ValueError(f"kind already registered: {kind}")
            cls.kind = kind
            self._models[kind] = cls
            return cls

        return deco

    def get(self, kind: str) -> ComponentModel:
        if kind not in self._models:
            raise KeyError(f"unknown component kind: {kind}")
        return self._models[kind]()

    def kinds(self) -> list[str]:
        return sorted(self._models)


REGISTRY = Registry()
register = REGISTRY.register
get = REGISTRY.get
kinds = REGISTRY.kinds


class Fleet:
    """n units per kind with vectorised state; advances one flight cycle at a time."""

    def __init__(self, seed: int, counts: dict[str, int], unit_tail: dict[str, np.ndarray] | None = None, registry: Registry = REGISTRY) -> None:
        self.seed = int(seed)
        self.counts = dict(counts)
        self.cycle = 0
        self.registry = registry
        self.models: dict[str, ComponentModel] = {k: registry.get(k) for k in sorted(counts)}
        self.unit_tail = {k: np.asarray(v, dtype=np.int64) for k, v in (unit_tail or {}).items()}
        self._rng = {k: {p: StreamRng(self.seed, f"{k}.{p}") for p in ("init", "step", "emit", "maint")} for k in self.models}
        self.state: dict[str, State] = {k: m.init(counts[k], self._rng[k]["init"].at(0)) for k, m in self.models.items()}

    # ---- context routing ----
    def _ctx_for(self, kind: str, ctx: Any) -> "OperatingContext":
        if isinstance(ctx, dict):
            return ctx[kind]
        n = self.counts[kind]
        if ctx.n == n and kind not in self.unit_tail:
            return ctx
        if kind not in self.unit_tail:
            raise ValueError(f"context has {ctx.n} units but kind {kind} has {n}; pass unit_tail")
        return ctx.take(self.unit_tail[kind])

    # ---- simulation ----
    def step_cycle(self, ctx: Any) -> None:
        for k, m in self.models.items():
            m.step(self.state[k], self._ctx_for(k, ctx), self._rng[k]["step"].at(self.cycle))
        self.cycle += 1

    def emit_all(self, ctx: Any) -> dict[str, dict[str, np.ndarray]]:
        """Telemetry for the cycle just stepped (noise keyed on cycle - 1, so it is reproducible)."""
        c = max(self.cycle - 1, 0)
        return {k: m.emit(self.state[k], self._ctx_for(k, ctx), self._rng[k]["emit"].at(c)) for k, m in self.models.items()}

    def health_index(self) -> dict[str, np.ndarray]:
        return {k: m.health_index(self.state[k]) for k, m in self.models.items()}

    def maintain(self, kind: str, idx: Any, action: str, q: float = 0.5) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown action: {action}")
        idx = np.atleast_1d(np.asarray(idx, dtype=np.int64))
        self.models[kind].maintain(self.state[kind], idx, action, float(q), self._rng[kind]["maint"].at(self.cycle))

    # ---- persistence ----
    def config_hash(self) -> str:
        cfg = {"seed": self.seed, "counts": self.counts, "models": {k: [type(m).__name__, list(m.channels)] for k, m in self.models.items()}}
        return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()

    def snapshot(self) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {"_meta/cycle": np.array(self.cycle, dtype=np.int64), "_meta/config_hash": np.array(self.config_hash())}
        for k, st in self.state.items():
            for name, arr in st.items():
                out[f"{k}/{name}"] = np.array(arr, copy=True)
        return out

    def restore(self, snap: dict[str, np.ndarray]) -> None:
        if str(snap["_meta/config_hash"]) != self.config_hash():
            raise ValueError("snapshot config hash does not match this fleet")
        self.cycle = int(snap["_meta/cycle"])
        for k in self.state:
            prefix = f"{k}/"
            self.state[k] = {key[len(prefix):]: np.array(v, copy=True) for key, v in snap.items() if key.startswith(prefix)}

    def state_hash(self) -> str:
        h = hashlib.sha256()
        snap = self.snapshot()
        for key in sorted(snap):
            a = np.ascontiguousarray(snap[key])
            h.update(key.encode())
            h.update(str(a.dtype).encode())
            h.update(a.tobytes())
        return h.hexdigest()
