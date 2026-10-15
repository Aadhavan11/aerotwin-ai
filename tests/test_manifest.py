"""Reproducibility stamps, canonical parameter hashing and additive integrations."""
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from app.insights import reliability_pack as rp
from app.sim.v2 import manifest
from app.sim.v2.models import brakes
from app.sim.v2.runner import Runner

SMALL = dict(years=0.05, seed=11, fleet=4, checkpoint_every=40)


@pytest.fixture
def rn(tmp_path):
    return Runner(tmp_path / "runs.db", tmp_path / "runs")


def test_build_manifest(monkeypatch):
    monkeypatch.setattr(manifest, "git_info", lambda: {"sha": "test", "dirty": False})
    stamp = manifest.build_manifest()
    assert isinstance(stamp["schema_version"], int) and stamp["schema_version"] == 1
    assert isinstance(stamp["created_at"], str)
    assert datetime.fromisoformat(stamp["created_at"]).utcoffset().total_seconds() == 0
    assert isinstance(stamp["git"], dict)
    assert isinstance(stamp["git"]["sha"], str) and isinstance(stamp["git"]["dirty"], bool)
    assert set(stamp["versions"]) == {"python", "numpy", "scipy", "sklearn"}
    assert all(isinstance(value, str) for value in stamp["versions"].values())
    assert set(stamp["flags"]) == {"SIM_ENGINE", "HEALTH_MODEL"}
    assert isinstance(stamp["seed"], int)
    assert isinstance(stamp["calibration"], dict) and isinstance(stamp["ml_artifacts"], dict)
    assert isinstance(stamp["data_provenance"], str)
    assert stamp["provenance"] == "SIMULATED"
    assert len(stamp["params_hash"]) == 64 and int(stamp["params_hash"], 16) >= 0
    json.dumps(stamp)
    override = manifest.build_manifest({"provenance": "REAL", "run_id": "run"}, created_at="fixed")
    assert override["provenance"] == "SIMULATED" and override["created_at"] == "fixed" and override["run_id"] == "run"


def test_params_hash_perturbation_and_restoration(monkeypatch):
    original = manifest.params_hash()
    assert manifest.params_hash() == original
    key = next(key for key, value in brakes.PARAMS.items() if isinstance(value, (int, float)))
    with monkeypatch.context() as patch:
        patch.setitem(brakes.PARAMS, key, brakes.PARAMS[key] + 1)
        assert manifest.params_hash() != original
    assert manifest.params_hash() == original


def test_params_hash_ordering(monkeypatch):
    original = manifest.params_hash()
    monkeypatch.setattr(brakes, "PARAMS", dict(reversed(list(brakes.PARAMS.items()))))
    assert manifest.params_hash() == original


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("git", 5),
                                   subprocess.CalledProcessError(1, "git"), OSError("unavailable")])
def test_no_git(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(manifest.subprocess, "run", fail)
    assert manifest.git_info() == {"sha": "unknown", "dirty": False}
    assert manifest.build_manifest()["git"] == {"sha": "unknown", "dirty": False}


def test_git_commands(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "abc\n" if len(calls) == 1 else " M tracked.py\n")

    monkeypatch.setattr(manifest.subprocess, "run", run)
    assert manifest.git_info() == {"sha": "abc", "dirty": True}
    assert calls[0][0] == ["git", "rev-parse", "HEAD"]
    assert calls[1][0] == ["git", "status", "--porcelain", "--untracked-files=no"]
    assert all(kwargs == dict(cwd=manifest.REPO_ROOT, timeout=5, capture_output=True, text=True, check=True) for _, kwargs in calls)


def test_file_hashes_and_missing_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, "REPO_ROOT", tmp_path)
    payload = b"a" * (1024 * 1024 + 7)
    (tmp_path / "large").write_bytes(payload)
    assert manifest.file_hashes(["large", tmp_path / "missing"]) == {
        "large": hashlib.sha256(payload).hexdigest(), "missing": None,
    }
    assert manifest.calibration_hashes() == {}
    assert manifest.ml_hashes() and all(value is None for value in manifest.ml_hashes().values())
    calibration = tmp_path / "app/sim/v2/calibration"
    calibration.mkdir(parents=True)
    for name in ("b.json", "a.json"):
        (calibration / name).write_bytes(b"{}")
    hashes = manifest.calibration_hashes()
    assert list(hashes) == ["a.json", "b.json"]
    assert set(hashes.values()) == {hashlib.sha256(b"{}").hexdigest()}
    engine_eval = tmp_path / "app/sim/v2/engine/eval.json"
    engine_eval.parent.mkdir()
    engine_eval.write_bytes(b"{}")
    assert manifest.ml_hashes()["app/sim/v2/engine/eval.json"] == hashlib.sha256(b"{}").hexdigest()


def test_optional_metadata(monkeypatch):
    from app.ml import cmapss

    def unavailable(*args):
        raise ImportError("missing")

    monkeypatch.setattr(manifest.importlib.metadata, "version", unavailable)
    assert set(manifest.library_versions().values()) == {manifest.platform.python_version(), "unknown"}
    monkeypatch.setattr(cmapss, "data_source", unavailable)
    assert manifest.data_provenance() == "unknown"


def test_stable_view():
    stamp = {"created_at": "now", "git": {"sha": "abc"}}
    stable = manifest.stable_view(stamp)
    assert "created_at" not in stable
    stable["git"]["sha"] = "changed"
    assert stamp == {"created_at": "now", "git": {"sha": "abc"}}


def test_runner_export(rn):
    rid = rn.create(start=False, **SMALL)
    assert rn.execute(rid)["status"] == "done"
    path = Path(rn._row(rid)["out_dir"]) / "manifest.json"
    assert path.exists()
    stamp = json.loads(path.read_text())
    assert {"provenance", "run_id", "spec", "cycles", "rows", "removals", "state_hash", "tails", "note", "reproducibility"} == set(stamp)
    assert stamp["run_id"] == rid and stamp["provenance"] == "SIMULATED"
    repro = stamp["reproducibility"]
    assert repro["params_hash"] == manifest.params_hash()
    assert repro["run_id"] == rid and repro["seed"] == SMALL["seed"]
    assert repro["fleet_size"] == SMALL["fleet"] and repro["years"] == SMALL["years"]


def test_reliability_hash_and_pack(monkeypatch):
    key = rp.config_hash(11, 0.15)
    assert key == rp.config_hash(11, 0.15)
    param = next(iter(brakes.PARAMS))
    with monkeypatch.context() as patch:
        patch.setitem(brakes.PARAMS, param, brakes.PARAMS[param] + 1)
        assert rp.config_hash(11, 0.15) != key
    assert rp.config_hash(11, 0.15) == key
    ds = rp.Dataset([], rp.Exposure({"avionics": 1}, {"avionics": 100.0}, {"avionics": 50.0}, 30.0), {"basis": "backfill"})
    monkeypatch.setattr(rp, "live_world", lambda: None)
    monkeypatch.setattr(rp, "cached_backfill", lambda: ds)
    monkeypatch.setattr(manifest, "git_info", lambda: {"sha": "test", "dirty": False})
    out = rp.pack(include_weibull=False)
    assert isinstance(out["manifest"], dict)
    assert out["manifest"]["params_hash"] == manifest.params_hash()
    assert "created_at" not in out["manifest"]
    assert out["manifest"]["cache_key"] == out["source"]["cache_key"]
    assert out == rp.pack(include_weibull=False)
