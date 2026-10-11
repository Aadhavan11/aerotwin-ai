"""Estimated cost of a plan, from a SIMULATED price list (order-of-magnitude, illustrative; not real prices or quotes).

A plan's estimated cost is what the work itself costs plus what getting it wrong costs:

* parts      price of the part a job consumes (``PART_PRICE_USD`` x quantity)
* labor      job hours x crew size x labor rate
* bay        job hours x hangar bay rate
* shipping   ``logistics_cost_usd``: parts shipped in from another station (already in the plan metrics)
* delay      hours a job runs past its ground window (``downtime_h``) x cost of an hour of delay
* deferral   an allowance for every task the plan cannot do before its deadline (late or unscheduled): the expected cost of the
             unplanned event that follows (an aircraft on the ground at the wrong station, a part rushed in)

Two plans that do the same work pay the same parts, labor and bay time; they differ in shipping, delay and deferral. That is the honest
reason the optimized plan costs less than the static baseline, and why the two tie when both do everything in time.
"""
from __future__ import annotations

from typing import Optional

from app.contracts.models import Assignment, Task

PART_PRICE_USD = {"HYD-PUMP": 38_000.0, "BRAKE-ASSY": 14_500.0, "AVIONICS-LRU": 27_000.0, "BORESCOPE-KIT": 2_400.0}  # borescope: tool-crib charge per use
LABOR_RATE_USD_H = 145.0
CREW_SIZE = 2
BAY_RATE_USD_H = 650.0
DELAY_COST_USD_H = 5_000.0
DEFERRAL_ALLOWANCE_USD = 30_000.0


def job_hours(a: Assignment) -> float:
    return max(0.0, (a.end - a.start).total_seconds() / 3600.0)


def job_cost(task: Optional[Task], a: Assignment, shipping_usd: float = 0.0) -> dict:
    """Cost of one scheduled job, broken down. ``total`` is parts + labor + bay + shipping + delay."""
    h = job_hours(a)
    parts = PART_PRICE_USD.get(task.required_part, 0.0) * task.part_qty if task and task.required_part else 0.0
    labor = h * CREW_SIZE * LABOR_RATE_USD_H
    bay = h * BAY_RATE_USD_H
    delay = a.extension_h * DELAY_COST_USD_H
    return {"parts": round(parts, 2), "labor": round(labor, 2), "bay": round(bay, 2), "shipping": round(shipping_usd, 2),
            "delay": round(delay, 2), "total": round(parts + labor + bay + shipping_usd + delay, 2)}


def total_cost(parts: float, labor: float, bay: float, shipping: float, delay: float, exposure: float) -> float:
    return round(parts + labor + bay + shipping + delay + exposure, 2)
