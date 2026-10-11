"""Database mode: after reset_db (seeded from fixtures), /fleet, /legs and /stations come from SQLite."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, settings
from app.contracts.models import *  # noqa: F401,F403


@pytest.fixture()
def dbclient(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(settings, "MOCK", False)
    from app import registry
    monkeypatch.setitem(registry.MODULES, "sim", "real")  # database mode is only used when the real simulator is running
    from scripts import reset_db
    monkeypatch.setattr("sys.argv", ["reset_db"])
    reset_db.main()
    from app.main import app
    return TestClient(app)


def test_fleet_from_database(dbclient):
    r = dbclient.get("/fleet")
    assert r.status_code == 200 and r.headers["x-data-source"] == "database"
    f = FleetOverview.model_validate(r.json())
    assert f.kpis.aircraft == 24 and len(f.stations) == 8


def test_legs_and_stations_from_database(dbclient):
    legs = dbclient.get("/legs?tail=SIM-017")
    assert legs.headers["x-data-source"] == "database" and len(legs.json()) == 20
    assert len(dbclient.get("/stations").json()) == 8
    assert dbclient.get("/health").json()["data_source"] == "database"
