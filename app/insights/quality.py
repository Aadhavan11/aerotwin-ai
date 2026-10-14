"""GET /insights/data-quality: per-component sensor data quality from the latest telemetry and the sensor-fault state."""
from __future__ import annotations

import numpy as np

from app.contracts.models import ComponentState
from app.ml.health import series_issues
from app.ml.sensors import TELEMETRY_KEYS, kind_from_component_id
from app.insights import source

STUCK_K = 8   # engines have coarse, quantised sensors: demand a longer constant run than the generic 5


def _matrix(rows, keys) -> np.ndarray:
    out = np.full((len(rows), len(keys)), np.nan)
    for i, r in enumerate(rows):
        for j, k in enumerate(keys):
            v = r.values.get(k)
            if v is not None:
                try:
                    out[i, j] = float(v)
                except (TypeError, ValueError):
                    pass
    return out


def assess(component: ComponentState) -> dict:
    kind = kind_from_component_id(component.component_id)
    keys = TELEMETRY_KEYS.get(kind, [])
    rows = source.telemetry(component)
    reasons: list[str] = []
    found: dict[tuple[str, str], None] = {}
    wear_bad = False
    if not rows:
        reasons.append("no telemetry rows yet")
    else:
        mat = _matrix(rows, keys)
        for j, key in enumerate(keys):
            x = mat[:, j]
            valid = x[~np.isnan(x)]
            if len(valid) >= 10 and float(np.std(valid)) == 0.0:
                issues = [("stuck", f"{key} flat for {len(valid)} cycles")]
            else:
                issues = series_issues(key, x, last_k=5 if kind != "engine" else STUCK_K)
            for mode, why in issues:
                found[(key, mode)] = None
                reasons.append(why)
                if j == 0 and mode in ("dropout", "stuck"):
                    wear_bad = True
    for f in source.fault_state(component.component_id):
        if (f["key"], f["mode"]) not in found:
            found[(f["key"], f["mode"])] = None
            reasons.append(f"{f['key']} sensor fault: {f['mode']}")
    affected = {k for k, _ in found}
    if not rows:
        status = "ok"   # nothing has flown yet: a missing history is not a sensor fault
    elif wear_bad or len(affected) >= 2:
        status = "bad"
    elif found:
        status = "degraded"
    else:
        status = "ok"
    return {"tail": component.tail, "component_id": component.component_id, "kind": kind, "status": status,
            "reasons": reasons, "faults": [{"key": k, "mode": m} for k, m in sorted(found)]}


def report() -> dict:
    items = [assess(c) for c in sorted(source.components(), key=lambda c: (c.tail, c.component_id))]
    counts = {"ok": 0, "degraded": 0, "bad": 0}
    for it in items:
        counts[it["status"]] += 1
    return {"simulated": True, "as_of": source.as_of(), "counts": counts, "items": items}
