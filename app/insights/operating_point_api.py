"""GET /insights/operating-point: per-kind alert threshold chosen from the operator's costs (SIMULATED, advisory).

Cost parameters are illustrative priors (see ``app.ml.operating_point``); the airline must set its own.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Query

from app.ml import operating_point as op

router = APIRouter(tags=["insights"])
_D = op.CostParams()


@router.get("/insights/operating-point")
def operating_point(
    false_alarm_cost: float = Query(_D.false_alarm_cost, ge=0, allow_inf_nan=False),
    miss_cost: float = Query(_D.miss_cost, ge=0, allow_inf_nan=False),
    lead_value: float = Query(_D.lead_value, ge=0, allow_inf_nan=False),
    max_false_alarms_per_1000fh: float = Query(_D.max_false_alarms_per_1000fh, ge=0, allow_inf_nan=False),
    min_lead_cycles: float = Query(_D.min_lead_cycles, ge=0, allow_inf_nan=False),
    lead_cap_cycles: float = Query(_D.lead_cap_cycles, ge=0, allow_inf_nan=False),
    mode: Literal["quick", "full"] = "quick",
    n_boot: Optional[int] = Query(200, ge=200, le=2000),
):
    p = op.CostParams(false_alarm_cost, miss_cost, lead_value, lead_cap_cycles, max_false_alarms_per_1000fh, min_lead_cycles)
    return op.result_for(p, mode, n_boot or 200)
