"""Deterministic sensor faults with JSON-serializable, caller-owned state."""

from __future__ import annotations

import random

MODES: tuple[str, ...] = ("dropout", "stuck", "drift", "spike", "noise")


def empty_state() -> dict:
    """Return independent fault and last-good-value mappings."""
    return {"faults": {}, "last": {}}


def _copy_state(state: dict) -> dict:
    return {
        "faults": {key: dict(fault) for key, fault in state["faults"].items()},
        "last": dict(state["last"]),
    }


def start_fault(
    state: dict, key: str, mode: str, cycle: int, duration: int, magnitude: float
) -> dict:
    """Add or replace a fault active over [cycle, cycle + duration)."""
    if mode not in MODES:
        raise ValueError(f"Unknown fault mode: {mode!r}")
    if duration < 1:
        raise ValueError("Fault duration must be at least 1")
    result = _copy_state(state)
    result["faults"][key] = {
        "mode": mode, "start": cycle, "duration": duration, "magnitude": magnitude
    }
    return result


def stochastic_onset(rng: random.Random, rate_per_cycle: float) -> bool:
    """Sample an onset using only the supplied random generator."""
    if not 0.0 <= rate_per_cycle <= 1.0:
        raise ValueError("Onset rate must be in [0, 1]")
    return rng.random() < rate_per_cycle


def apply(
    component_id: str,
    cycle: int,
    values: dict[str, float],
    state: dict,
    rng: random.Random,
) -> tuple[dict, dict]:
    """Apply faults to one component's values without mutating inputs.

    State is scoped by the caller to component_id. Only active noise faults
    on present sensor keys consume randomness.
    """
    result = _copy_state(state)
    result["faults"] = {
        key: fault for key, fault in result["faults"].items()
        if cycle < fault["start"] + fault["duration"]
    }
    out = {}
    for key, value in values.items():
        fault = result["faults"].get(key)
        if fault is None or cycle < fault["start"]:
            out[key] = value
            result["last"][key] = value
            continue
        mode = fault["mode"]
        if mode == "dropout":
            continue
        if mode == "stuck":
            if "frozen" not in fault:
                fault["frozen"] = result["last"].get(key, value)
            out[key] = fault["frozen"]
        elif mode == "drift":
            out[key] = value + fault["magnitude"] * (cycle - fault["start"] + 1)
        elif mode == "spike":
            out[key] = value + fault["magnitude"] if cycle == fault["start"] else value
        elif mode == "noise":
            out[key] = value + rng.gauss(0.0, fault["magnitude"])
    return out, result


def faults_active(state: dict) -> list[dict]:
    """List stored faults in sensor-key order."""
    return [{"key": key, "mode": state["faults"][key]["mode"]}
            for key in sorted(state["faults"])]
