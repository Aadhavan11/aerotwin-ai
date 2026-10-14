"""The fictional MEL register: time boundaries, persistence, decisions and access."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import db, settings
from app.auth.core import AuthGate
from app.copilot import audit, persist, reset_all
from app.insights.router import router as insights_router
from app.ops import mel
from test_auth import login, on as on

NOW = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)
KEYS = {"A": ("avionics", "fault"), "B": ("apu", "failed-to-start"), "C": ("ecs pack 1", "fault"), "D": ("tires", "fault")}


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "mel.sqlite")
    monkeypatch.setattr(persist, "_OVERRIDE", True)
    db.init_db()
    db.set_sim_now(NOW)
    yield


def fresh_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(AuthGate)
    app.include_router(insights_router)
    return TestClient(app)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    return fresh_client()


def make(category: str = "B", tail: str = "SIM-001", limit_days: int | None = None) -> mel.MelItem:
    kind, mode = KEYS[category]
    return mel.create_item(tail, kind, mode, "Fictional defect", "Planner", limit_days=limit_days)


def body(category: str = "B") -> dict[str, str | int]:
    kind, mode = KEYS[category]
    result: dict[str, str | int] = {"tail": "SIM-001", "component_kind": kind, "fault_mode": mode, "reason": "Fictional defect", "notes": "Demo"}
    if category == "A":
        result["limit_days"] = 2
    return result


@pytest.mark.parametrize(("category", "days"), [("A", 2), ("B", 3), ("C", 10), ("D", 120)])
def test_expiry_boundaries(category: str, days: int) -> None:
    item = make(category, limit_days=days if category == "A" else None)
    assert item.deferred_at == NOW
    assert item.expires_at == NOW + timedelta(days=days)
    db.set_sim_now(item.expires_at - timedelta(seconds=1))
    assert mel.get_item(item.id).status == "open"
    db.set_sim_now(item.expires_at)
    assert mel.get_item(item.id).status == "expired"
    assert mel.list_items(status="open") == []
    with db.conn() as c:
        assert c.execute("SELECT status FROM mel_items WHERE id=?", (item.id,)).fetchone()["status"] == "expired"
    db.set_sim_now(NOW)
    assert mel.get_item(item.id).status == "expired"


@pytest.mark.parametrize("limit", [None, 0, -1, True, 1.5])
def test_a_requires_positive_integer_limit(limit: int | None) -> None:
    with pytest.raises(ValueError, match="positive integer limit_days"):
        make("A", limit_days=limit)
    assert mel.list_items() == []
    assert audit.list_entries() == []


def test_model_requires_a_limit_and_simulated_true() -> None:
    data = make("A", limit_days=2).model_dump()
    data["limit_days"] = None
    with pytest.raises(ValidationError, match="category A"):
        mel.MelItem.model_validate(data)
    data["limit_days"], data["simulated"] = 2, False
    with pytest.raises(ValidationError, match="simulated"):
        mel.MelItem.model_validate(data)


@pytest.mark.parametrize(("category", "original", "extra"), [("B", 3, 1), ("C", 10, 5)])
def test_extension_once_from_original_deadline(category: str, original: int, extra: int) -> None:
    item = make(category)
    db.set_sim_now(NOW + timedelta(days=1))
    extended = mel.extend_item(item.id, "Planner", " Awaiting fictional parts ")
    assert extended.expires_at == NOW + timedelta(days=original + extra)
    assert extended.expires_at <= NOW + timedelta(days=original * 2)
    assert extended.extensions_used == 1
    assert mel.get_item(item.id) == extended
    with pytest.raises(mel.MelError, match="one extension"):
        mel.extend_item(item.id, "Planner", "Again")
    db.set_sim_now(extended.expires_at)
    assert mel.get_item(item.id).status == "expired"


@pytest.mark.parametrize("category", ["A", "D"])
def test_nonextendable_categories(category: str) -> None:
    item = make(category, limit_days=2 if category == "A" else None)
    with pytest.raises(mel.MelError, match=f"category {category} cannot be extended"):
        mel.extend_item(item.id, "Planner", "Parts")


@pytest.mark.parametrize("justification", ["", " \t\n "])
def test_justification_required(justification: str) -> None:
    item = make()
    with pytest.raises(ValueError, match="non-empty justification"):
        mel.extend_item(item.id, "Planner", justification)
    assert mel.get_item(item.id).extensions_used == 0
    assert len(audit.list_entries()) == 1


@pytest.mark.parametrize(("category", "period"), [("B", 3), ("C", 10)])
def test_twice_original_cap_is_enforced(category: str, period: int, monkeypatch: pytest.MonkeyPatch) -> None:
    item = make(category)
    monkeypatch.setitem(mel.EXTENSION_DAYS, category, period + 1)
    with pytest.raises(mel.MelError, match="twice the original period"):
        mel.extend_item(item.id, "Planner", "Parts")
    assert mel.get_item(item.id) == item
    assert len(audit.list_entries()) == 1
    monkeypatch.setitem(mel.EXTENSION_DAYS, category, period)
    assert mel.extend_item(item.id, "Planner", "Parts").expires_at == NOW + timedelta(days=2 * period)


def test_rectification_is_terminal() -> None:
    item = make()
    db.set_sim_now(NOW + timedelta(hours=1))
    closed = mel.close_item(item.id, "Technician")
    assert closed.status == "rectified" and closed.rectified_at == NOW + timedelta(hours=1)
    with pytest.raises(mel.MelError, match="rectified"):
        mel.close_item(item.id, "Technician")
    with pytest.raises(mel.MelError, match="rectified"):
        mel.extend_item(item.id, "Technician", "Parts")
    db.set_sim_now(item.expires_at)
    assert mel.get_item(item.id) == closed
    assert len(audit.list_entries()) == 2


def test_expired_items_cannot_be_closed_or_extended() -> None:
    item = make()
    db.set_sim_now(item.expires_at)
    with pytest.raises(mel.MelError, match="expired"):
        mel.close_item(item.id, "Planner")
    with pytest.raises(mel.MelError, match="expired"):
        mel.extend_item(item.id, "Planner", "Parts")
    assert mel.get_item(item.id).rectified_at is None
    assert len(audit.list_entries()) == 1


def test_catalogue_and_unknown_template() -> None:
    assert len(mel.CATALOGUE) == 6
    apu = mel.create_item("SIM-001", "APU", "failed-to-start", "Defect", "Planner")
    assert apu.ata_chapter == "49" and apu.ata_section == "49-40"
    assert apu.title == apu.restrictions[0] == "APU inoperative: ground power unit required at turn"
    anti_skid = mel.create_item("SIM-001", "Anti-skid", "fault", "Defect", "Planner")
    assert anti_skid.ata_chapter == "32" and anti_skid.ata_section == "32-42"
    assert anti_skid.restrictions == ["Anti-skid inoperative: contaminated-runway limits"]
    with pytest.raises(ValueError, match="unknown MEL template"):
        mel.create_item("SIM-001", "unknown", "fault", "Defect", "Planner")
    with pytest.raises(ValueError, match="must match template"):
        mel.create_item("SIM-001", "apu", "failed-to-start", "Defect", "Planner", category="D")


def test_audit_for_each_decision() -> None:
    first, second = make(), make("C")
    mel.extend_item(second.id, "Dispatcher", " Awaiting parts ")
    mel.close_item(first.id, "Technician")
    entries = audit.list_entries(newest_first=False)
    assert [entry.action for entry in entries] == ["mel_created", "mel_created", "mel_extended", "mel_closed"]
    assert [(entry.actor, entry.ref) for entry in entries] == [("Planner", first.id), ("Planner", second.id), ("Dispatcher", second.id), ("Technician", first.id)]
    assert entries[2].detail == "Awaiting parts"
    assert all(entry.ts == NOW for entry in entries)


def test_summary_and_restrictions_refresh(client: TestClient) -> None:
    a = make("A", "SIM-001", limit_days=1)
    b = make("B", "SIM-001")
    c = make("C", "SIM-002")
    d = make("D", "SIM-003")
    closed = make("B", "SIM-004")
    mel.close_item(closed.id, "Planner")
    summary = client.get("/ops/mel/summary").json()
    assert summary == {"simulated": True, "by_category": {"A": 1, "B": 1, "C": 1, "D": 1}, "expiring_within_72h": 2,
                       "aircraft_with_open_restrictions": ["SIM-001", "SIM-002", "SIM-003"]}
    assert mel.open_restrictions("SIM-001") == a.restrictions + b.restrictions
    assert mel.open_restrictions("SIM-002") == c.restrictions
    assert mel.open_restrictions("SIM-003") == d.restrictions
    assert mel.open_restrictions("SIM-004") == []
    assert mel.open_restrictions("unknown") == []
    db.set_sim_now(b.expires_at)
    assert mel.open_restrictions("SIM-001") == []
    summary = client.get("/ops/mel/summary").json()
    assert summary["by_category"] == {"A": 0, "B": 0, "C": 1, "D": 1}
    assert summary["expiring_within_72h"] == 0
    assert summary["aircraft_with_open_restrictions"] == ["SIM-002", "SIM-003"]


def test_persistence_and_sequence_across_fresh_apps(client: TestClient) -> None:
    first = client.post("/ops/mel", json=body()).json()
    assert first["id"] == "MEL-0001"
    mel.extend_item(first["id"], "Planner", "Parts")
    restarted = fresh_client()
    saved = restarted.get(f"/ops/mel/{first['id']}").json()
    assert saved["extensions_used"] == 1 and saved["notes"] == "Demo"
    assert restarted.post("/ops/mel", json=body("C")).json()["id"] == "MEL-0002"
    assert len(audit.list_entries()) == 3
    db.init_db()
    assert len(mel.list_items()) == 2


def test_scenario_reset_clears_items_preserving_sequence() -> None:
    make()
    reset_all()
    assert mel.list_items() == []
    assert make().id == "MEL-0002"


@pytest.mark.parametrize("username", ["operations", "maintenance", "administrator"])
def test_allowed_roles_can_make_all_decisions(on: TestClient, username: str) -> None:
    assert login(on, username).status_code == 200
    response = on.post("/ops/mel", json=body())
    assert response.status_code == 200
    item = response.json()
    assert item["deferred_by"] == on.get("/auth/me").json()["user"]["name"]
    assert on.post(f"/ops/mel/{item['id']}/extend", json={"justification": "Parts"}).status_code == 200
    assert on.post(f"/ops/mel/{item['id']}/close").status_code == 200
    assert all(entry.actor == item["deferred_by"] for entry in audit.list_entries())


@pytest.mark.parametrize("username", ["crew", "dispatch"])
def test_other_roles_can_read_but_not_write(on: TestClient, username: str) -> None:
    item = make()
    assert login(on, username).status_code == 200
    for path in ("/ops/mel", "/ops/mel/summary", f"/ops/mel/{item.id}"):
        assert on.get(path).status_code == 200
    assert on.post("/ops/mel", json=body()).status_code == 403
    assert on.post(f"/ops/mel/{item.id}/close").status_code == 403
    assert on.post(f"/ops/mel/{item.id}/extend", json={"justification": "Parts"}).status_code == 403


def test_unauthenticated_requests_denied(on: TestClient) -> None:
    for path in ("/ops/mel", "/ops/mel/summary", "/ops/mel/MEL-0001"):
        assert on.get(path).status_code == 401
    assert on.post("/ops/mel", json=body()).status_code == 401
    assert on.post("/ops/mel/MEL-0001/close").status_code == 401
    assert on.post("/ops/mel/MEL-0001/extend", json={"justification": "Parts"}).status_code == 401


def test_all_success_routes_are_simulated(client: TestClient) -> None:
    created = client.post("/ops/mel", json=body()).json()
    item_id = created["id"]
    assert created["simulated"] is True
    for path in ("/ops/mel", "/ops/mel/summary", f"/ops/mel/{item_id}"):
        response = client.get(path)
        assert response.status_code == 200 and response.json()["simulated"] is True
    extended = client.post(f"/ops/mel/{item_id}/extend", json={"justification": "Parts"})
    assert extended.status_code == 200 and extended.json()["simulated"] is True
    closed = client.post(f"/ops/mel/{item_id}/close")
    assert closed.status_code == 200 and closed.json()["simulated"] is True
    items = client.get("/ops/mel", params={"tail": "SIM-001", "status": "rectified"}).json()["items"]
    assert [item["id"] for item in items] == [item_id] and all(item["simulated"] is True for item in items)
    assert client.get("/ops/mel", params={"tail": "SIM-002"}).json()["items"] == []


@pytest.mark.parametrize("path", ["/ops/mel/missing", "/ops/mel/missing/close", "/ops/mel/missing/extend"])
def test_not_found_is_404(client: TestClient, path: str) -> None:
    response = client.get(path) if path.endswith("missing") else client.post(path, json={"justification": "Parts"})
    assert response.status_code == 404 and response.json()["simulated"] is True


def test_api_errors_are_validation_or_conflict(client: TestClient) -> None:
    invalid_bodies = [body("A") | {"limit_days": None}, body("A") | {"limit_days": 0}, body("A") | {"limit_days": 1.5},
                      body() | {"category": "A"}, body() | {"category": "X"}, body() | {"component_kind": "unknown"}, body() | {"tail": " "}]
    for payload in invalid_bodies:
        response = client.post("/ops/mel", json=payload)
        assert response.status_code == 422 and response.json()["simulated"] is True
    assert client.get("/ops/mel?status=invalid").status_code == 422
    item = client.post("/ops/mel", json=body()).json()
    for payload in ({}, {"justification": ""}, {"justification": " "}):
        response = client.post(f"/ops/mel/{item['id']}/extend", json=payload)
        assert response.status_code == 422 and response.json()["simulated"] is True
    client.post(f"/ops/mel/{item['id']}/close")
    for suffix in ("close", "extend"):
        response = client.post(f"/ops/mel/{item['id']}/{suffix}", json={"justification": "Parts"})
        assert response.status_code == 409 and response.json()["simulated"] is True
    for category in ("A", "D"):
        item = client.post("/ops/mel", json=body(category)).json()
        assert client.post(f"/ops/mel/{item['id']}/extend", json={"justification": "Parts"}).status_code == 409
