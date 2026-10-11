"""The MOCK API serves every endpoint in CONTRACTS.md section 4 with valid models, and the golden path passes on it."""
import pytest
from fastapi.testclient import TestClient

from app.contracts.models import *  # noqa: F401,F403
from app.main import app
from scripts import golden


class Adapter:
    def __init__(self, c): self.c = c
    def get(self, p): r = self.c.get(p); return r.status_code, r.json()
    def post(self, p, b=None): r = self.c.post(p, json=b or {}); return r.status_code, r.json()


@pytest.fixture()
def client():
    c = TestClient(app)
    c.post("/scenario/reset", json={"name": "golden"})
    return c


def test_health_lists_modules(client):
    j = client.get("/health").json()
    assert j["ok"] and j["mock"] and set(j["modules"]) == {"twin", "solver", "whatif", "sim", "agents", "copilot"}
    assert j["ml_data"] in ("nasa-cmapss-fd001", "synthetic-cmapss-shaped")  # the app shows a banner when it is the synthetic stand-in


@pytest.mark.parametrize("path,model", [("/fleet", FleetOverview), ("/aircraft/SIM-017", TwinView), ("/aircraft/SIM-005", TwinView), ("/stations", list[Station]), ("/legs?tail=SIM-017", list[Leg]),
                                         ("/metrics", MetricsBundle), ("/audit", list[AuditEntry]), ("/workorders", list[WorkOrder])])
def test_get_endpoints_validate(client, path, model):
    from pydantic import TypeAdapter
    r = client.get(path)
    assert r.status_code == 200, r.text
    TypeAdapter(model).validate_python(r.json())


def test_fleet_follows_mock_state_even_when_db_is_seeded(client, tmp_path, monkeypatch):
    """Regression: with a seeded DB but a MOCK simulator, an injected anomaly must still show up in /fleet."""
    from app import settings
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "x.db")
    from scripts import reset_db
    monkeypatch.setattr("sys.argv", ["reset_db"])
    reset_db.main()
    monkeypatch.setattr(settings, "MOCK", False)
    client.post("/scenario/reset", json={"name": "golden"})
    client.post("/scenario/inject", json={"type": "anomaly", "at": "2026-10-12T12:00:00Z", "tail": "SIM-017", "component_id": "SIM-017/engine_2", "severity": 0.9})
    assert next(a for a in client.get("/fleet").json()["aircraft"] if a["tail"] == "SIM-017")["status"] == "alert"


def test_unknown_tail_404(client):
    assert client.get("/aircraft/NOPE").status_code == 404


def test_inject_changes_fleet_and_twin(client):
    assert next(a for a in client.get("/fleet").json()["aircraft"] if a["tail"] == "SIM-017")["status"] == "ok"
    client.post("/scenario/inject", json={"type": "anomaly", "at": "2026-10-12T12:00:00Z", "tail": "SIM-017", "component_id": "SIM-017/engine_2", "severity": 0.9})
    assert next(a for a in client.get("/fleet").json()["aircraft"] if a["tail"] == "SIM-017")["status"] == "alert"
    eng = next(c for c in client.get("/aircraft/SIM-017").json()["components"] if c["component_id"] == "SIM-017/engine_2")
    assert eng["status"] == "alert" and eng["rul_cycles"]["p10"] == 18


def test_compare_returns_a_row_per_scenario(client):
    r = client.post("/whatif/compare", json={"scenarios": [{"name": "base"}, {"name": "kit late", "events": [{"type": "part_delay", "at": "2026-10-12T14:00:00Z", "station": "DFW", "part": "BORESCOPE-KIT", "delay_hours": 12}]}]})
    assert r.status_code == 200 and [x["name"] for x in r.json()["rows"]] == ["base", "kit late"]


def test_golden_path_on_mock(client):
    res = golden.run(Adapter(client), poll_timeout=60, verbose=False)
    failed = [(n, d) for n, ok, d in res if not ok]
    assert not failed, failed
