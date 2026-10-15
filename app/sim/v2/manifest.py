"""Reproducibility stamps for simulation exports and reliability packs."""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import subprocess
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
VOLATILE_KEYS = ("created_at",)
REPO_ROOT = Path(__file__).resolve().parents[3]
# Explicit simulation/analysis parameters; imports are deferred to avoid runner cycles.
SIMULATION_CONSTANTS = {
    "app.sim.constants": (
        "FLIGHTS_PER_DAY", "BLOCK_HOURS_PER_FLIGHT", "DEFAULT_LEGS_PER_DAY", "ENGINE_CMAPSS_MEAN_LIFE_CYCLES",
        "ENGINE_FLIGHTS_PER_MODEL_CYCLE", "ENGINE_MEAN_ON_WING_FLIGHTS", "ENGINE_MODEL_LLP_LIMIT_CYCLES", "LLP_CYCLE_LIMITS",
    ),
    "app.sim.v2.drivers": ("LEGS_PER_DAY", "BASE_CYCLES_PER_DAY"),
    "app.sim.v2.runner": ("CYCLES_PER_YEAR", "DEFAULT_SEGMENT"),
    "app.sim.v2.world": (
        "SENSOR_FAULT_RATE", "FINDING_BELOW", "REPAIR_ABOVE", "NFF_WINDOW", "NFF_BITE_DELTA", "NFF_ESCALATE_N",
        "NFF_HEALTH_MIN", "MX_INDUCED_P", "MX_INDUCED_SEVERITY", "UNITS_PER_TAIL",
    ),
    "app.insights.reliability_pack": (
        "BACKFILL_YEARS", "PERIOD_DAYS", "WINDOW", "MIN_WINDOW", "MIN_EVENTS", "SIGMA_ALERT", "SIGMA_WARNING",
        "PREMATURE_HEALTH", "REPEAT_N", "REPEAT_DAYS", "CONFIDENCE",
    ),
}


def _json_default(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return sorted(value)
    if type(value).__module__.split(".")[0] == "numpy" and hasattr(value, "item"):
        return value.item()
    return repr(value)


def params_hash() -> str:
    """Hash every model PARAMS, world policy tables and the explicit constants above."""
    models = {}
    for path in sorted((Path(__file__).parent / "models").glob("*.py")):
        if path.stem.startswith("_"):
            continue
        module = importlib.import_module(f"app.sim.v2.models.{path.stem}")
        if hasattr(module, "PARAMS"):
            models[module.__name__] = module.PARAMS
    world = importlib.import_module("app.sim.v2.world")
    params = {
        "models": models,
        "world": {name: getattr(world, name) for name in ("POLICY", "FALSE_CALL", "INFANT")},
        "constants": {module: {name: getattr(importlib.import_module(module), name) for name in names}
                      for module, names in SIMULATION_CONSTANTS.items()},
    }
    return hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":"), default=_json_default).encode()).hexdigest()


def file_hashes(paths: Iterable[str | Path]) -> dict[str, str | None]:
    """Stream SHA-256 hashes with repository-relative POSIX keys; unavailable files are None."""
    hashes = {}
    for value in paths:
        path = Path(value)
        path = path if path.is_absolute() else REPO_ROOT / path
        key = Path(os.path.relpath(path, REPO_ROOT)).as_posix()
        try:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes[key] = digest.hexdigest()
        except OSError:
            hashes[key] = None
    return hashes


def calibration_hashes() -> dict[str, str | None]:
    """Hash available calibration JSON files, ordered by filename."""
    paths = sorted((REPO_ROOT / "app/sim/v2/calibration").glob("*.json"))
    return {Path(key).name: value for key, value in file_hashes(paths).items()}


def ml_hashes() -> dict[str, str | None]:
    """Hash generic joblib artifacts and evaluation metadata without loading pickles."""
    artifacts = REPO_ROOT / "app/ml/artifacts"
    paths = set(artifacts.glob("generic_*.joblib"))
    paths.update(artifacts / f"generic_{kind}.joblib" for kind in
                 ("apu", "avionics", "brakes", "ecs", "electrical", "hydraulics", "landing_gear", "tires"))
    paths.update(artifacts / name for name in ("generic_meta.json", "eval.json"))
    paths.update((REPO_ROOT / "app/sim/v2").rglob("eval.json"))
    return file_hashes(sorted(paths))


def git_info() -> dict[str, str | bool]:
    """Return commit and tracked-file dirty status, or a safe fallback if git fails."""
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, timeout=5, capture_output=True, text=True, check=True)
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                cwd=REPO_ROOT, timeout=5, capture_output=True, text=True, check=True)
        if not sha.stdout.strip():
            return {"sha": "unknown", "dirty": False}
        return {"sha": sha.stdout.strip(), "dirty": bool(status.stdout.strip())}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"sha": "unknown", "dirty": False}


def library_versions() -> dict[str, str]:
    """Record runtime versions without importing scientific libraries."""
    versions = {"python": platform.python_version()}
    for key, distribution in (("numpy", "numpy"), ("scipy", "scipy"), ("sklearn", "scikit-learn")):
        try:
            versions[key] = importlib.metadata.version(distribution)
        except (ImportError, OSError, ValueError):
            versions[key] = "unknown"
    return versions


def data_provenance() -> str:
    """Report NASA versus synthetic C-MAPSS data without requiring data files."""
    try:
        from app.ml.cmapss import data_source

        return data_source()
    except (ImportError, OSError, ValueError, RuntimeError):
        return "unknown"


def build_manifest(extra: dict | None = None, created_at: str | None = None) -> dict:
    """Build a JSON-compatible stamp; caller extras cannot change SIMULATED provenance."""
    from app import settings

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat() if created_at is None else created_at,
        "git": git_info(), "versions": library_versions(),
        "flags": {"SIM_ENGINE": settings.SIM_ENGINE,
                  "HEALTH_MODEL": getattr(settings, "HEALTH_MODEL", os.environ.get("HEALTH_MODEL", "rule").strip().lower())},
        "seed": settings.SEED, "params_hash": params_hash(), "calibration": calibration_hashes(),
        "ml_artifacts": ml_hashes(), "data_provenance": data_provenance(),
    }
    manifest.update(extra or {})
    manifest["provenance"] = "SIMULATED"
    return manifest


def stable_view(manifest: dict) -> dict:
    """Deep-copy a stamp without its volatile top-level fields."""
    return copy.deepcopy({key: value for key, value in manifest.items() if key not in VOLATILE_KEYS})
