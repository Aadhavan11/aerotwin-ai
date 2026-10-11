"""Sign-in: off by default; with AUTH_REQUIRED=1 data needs a session and roles are enforced on the server."""
import pytest
from fastapi.testclient import TestClient

from app.auth import core
from app.main import app


@pytest.fixture()
def on(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_FILE", tmp_path / "auth.sqlite")
    monkeypatch.setattr(core, "_conn", None)
    monkeypatch.setattr(core, "_fails", {})
    monkeypatch.setattr(core, "_master_mtime", 0.0)
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("AUTH_DEMO_PASSWORD", "demo-pass-1")
    monkeypatch.setenv("AUTH_MASTER_EMAILS", "boss@example.com")
    core.seed()
    yield TestClient(app)
    monkeypatch.setattr(core, "_conn", None)


def login(c, user, pw="demo-pass-1"):
    return c.post("/auth/login", json={"username": user, "password": pw})


def test_off_by_default():
    assert TestClient(app).get("/fleet").status_code == 200


def test_data_needs_a_session(on):
    assert on.get("/fleet").status_code == 401
    assert on.get("/health").status_code == 200
    cfg = on.get("/auth/config").json()
    assert cfg["enabled"] and {a["username"] for a in cfg["demo_accounts"]} == {"operations", "maintenance", "crew", "dispatch", "administrator"}
    assert on.get("/auth/me").json() == {"user": None}


def test_login_and_logout(on):
    assert login(on, "operations", "wrong").status_code == 401
    r = login(on, "operations")
    assert r.status_code == 200 and r.json()["user"]["role"] == "ops"
    assert on.get("/fleet").status_code == 200
    assert on.get("/auth/me").json()["user"]["username"] == "operations"
    on.post("/auth/logout")
    assert on.get("/fleet").status_code == 401


def test_roles_are_enforced_on_the_server(on):
    login(on, "crew")  # pilot / flight crew
    assert on.post("/workorders/WO-1/decision", json={"decision": "approve", "user": "x", "note": ""}).status_code == 403
    assert on.post("/scenario/reset", json={}).status_code == 403
    login(on, "operations")
    assert on.post("/workorders/WO-999/decision", json={"decision": "approve", "user": "x", "note": ""}).status_code != 403


def test_signup_validation_and_position(on):
    base = {"username": "new.user", "password": "longenough1", "name": "New User", "role": "dispatch"}
    assert on.post("/auth/signup", json={**base, "password": "short"}).status_code == 422
    assert on.post("/auth/signup", json={**base, "role": "king"}).status_code == 422
    assert on.post("/auth/signup", json={**base, "username": "ab"}).status_code == 422
    r = on.post("/auth/signup", json=base)
    assert r.status_code == 200 and r.json()["user"]["role"] == "dispatch"
    assert on.post("/auth/signup", json=base).status_code == 409
    assert on.get("/auth/me").json()["user"]["name"] == "New User"


def test_master_switches_positions(on, tmp_path, monkeypatch):
    assert login(on, "admin").status_code == 401  # no password until the file exists
    f = tmp_path / "pw"
    f.write_text("master-secret-9\n")
    monkeypatch.setenv("AUTH_MASTER_PASSWORD_FILE", str(f))
    assert login(on, "admin", "master-secret-9").status_code == 200
    assert on.post("/auth/role", json={"role": "pilot"}).json()["user"]["role"] == "pilot"
    assert on.get("/auth/me").json()["user"]["role"] == "pilot"
    assert on.post("/workorders/WO-1/decision", json={"decision": "approve", "user": "x", "note": ""}).status_code == 403
    on.post("/auth/role", json={"role": "ops"})
    assert on.post("/workorders/WO-999/decision", json={"decision": "approve", "user": "x", "note": ""}).status_code != 403
    assert any(u["username"] == "admin" for u in on.get("/auth/users").json())


def test_only_master_may_switch(on):
    login(on, "operations")
    assert on.post("/auth/role", json={"role": "pilot"}).status_code == 403
    assert on.get("/auth/users").status_code == 403


def test_google_flow(on, monkeypatch):
    info = {"sub": "123", "email": "Pat@Example.com", "name": "Pat Lee", "picture": None}
    monkeypatch.setattr(core, "verify_google", lambda cred: info)
    r = on.post("/auth/google", json={"credential": "x"})
    assert r.json()["needs_role"] is True and r.json()["email"] == "pat@example.com"
    r = on.post("/auth/google", json={"credential": "x", "role": "maintenance"})
    assert r.json()["user"]["role"] == "maintenance" and not r.json()["user"]["master"]
    on.post("/auth/logout")
    assert on.post("/auth/google", json={"credential": "x"}).json()["user"]["username"] == "pat"  # returning user: no role needed
    monkeypatch.setattr(core, "verify_google", lambda cred: {"sub": "9", "email": "boss@example.com", "name": "The Boss", "picture": None})
    r = on.post("/auth/google", json={"credential": "x"})
    assert r.json()["user"]["master"] is True
