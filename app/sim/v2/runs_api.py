"""HTTP surface of the v2 backfill runner: POST/GET /sim/runs, resume, export. Provenance is SIMULATED everywhere.

Mounted by app/sim/router.py. Role limits (ops/admin) are enforced by the AuthGate guards in app/auth/core.py.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.sim.v2 import runner as runner_mod

router = APIRouter()


class RunRequest(BaseModel):
    years: float = Field(gt=0, le=20, allow_inf_nan=False)
    seed: int = 1234
    fleet: int | list[str] | None = Field(default=None, description="tail count (SIM-001..) or explicit tails; default the 24-tail fleet")
    initial_wear: bool = True
    checkpoint_every: int = Field(default=runner_mod.DEFAULT_SEGMENT, ge=1)


def _runner() -> runner_mod.Runner:
    return runner_mod.get_runner()


def _get(fn, *args) -> Any:
    try:
        return fn(*args)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown run") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/sim/runs", status_code=202)
def create_run(req: RunRequest) -> dict[str, Any]:
    if isinstance(req.fleet, int) and not 1 <= req.fleet <= 24:
        raise HTTPException(status_code=422, detail="fleet must be 1..24 tails")
    rid = _runner().create(req.years, req.seed, req.fleet, req.initial_wear, req.checkpoint_every)
    return _runner().status(rid)


@router.get("/sim/runs")
def list_runs() -> list[dict[str, Any]]:
    return _runner().list()


@router.get("/sim/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return _get(_runner().status, run_id)


@router.post("/sim/runs/{run_id}/resume", status_code=202)
def resume_run(run_id: str) -> dict[str, Any]:
    return _get(_runner().resume, run_id)


@router.get("/sim/runs/{run_id}/export")
def export_run(run_id: str) -> FileResponse:
    path = _get(_runner().export_path, run_id)
    media = "application/vnd.apache.parquet" if path.suffix == ".parquet" else "application/gzip"
    return FileResponse(path, media_type=media, filename=f"sim_run_{run_id}{''.join(path.suffixes)}",
                        headers={"X-Data-Source": "SIMULATED"})
