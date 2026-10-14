"""Reference model (tests only): linear wear with multiplicative noise and a noisy sensor."""

from __future__ import annotations

import numpy as np

from app.sim.v2.core import State, register
from app.sim.v2.drivers import OperatingContext


@register("_reference")
class ReferenceModel:
    kind = "_reference"
    channels = ("ref_signal",)

    def _params(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return 0.002 * rng.lognormal(0.0, 0.25, n)

    def init(self, n: int, rng: np.random.Generator) -> State:
        return {
            "wear_rate": self._params(n, rng),
            "damage": np.zeros(n),
            "age_cycles": np.zeros(n),
            "age_hours": np.zeros(n),
            "age_virtual": np.zeros(n),
            "hi": np.ones(n),
            "failed": np.zeros(n, dtype=bool),
        }

    def step(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> None:
        inc = state["wear_rate"] * (1.0 + 0.1 * np.abs(rng.standard_normal(ctx.n)))
        state["damage"] += inc
        state["age_cycles"] += 1
        state["age_virtual"] += 1
        state["age_hours"] += ctx.flight_hours
        state["hi"] = np.clip(1.0 - state["damage"], 0.0, 1.0)
        state["failed"] = state["hi"] <= 0.0

    def emit(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> dict[str, np.ndarray]:
        return {"ref_signal": state["hi"] + 0.01 * rng.standard_normal(ctx.n)}

    def health_index(self, state: State) -> np.ndarray:
        return state["hi"]

    def maintain(self, state: State, idx: np.ndarray, action: str, q: float, rng: np.random.Generator) -> None:
        if action == "inspect" or idx.size == 0:
            return
        if action in ("repair", "overhaul"):
            state["damage"][idx] *= q
            state["age_virtual"][idx] *= q
        elif action == "replace":
            state["wear_rate"][idx] = self._params(idx.size, rng)
            for key in ("damage", "age_cycles", "age_hours", "age_virtual"):
                state[key][idx] = 0.0
            state["failed"][idx] = False
        else:
            raise ValueError(f"unknown action: {action}")
        state["hi"][idx] = np.clip(1.0 - state["damage"][idx], 0.0, 1.0)
