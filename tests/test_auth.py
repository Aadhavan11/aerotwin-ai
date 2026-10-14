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


def test_signup_validation_and_position(on, monkeypatch):
    monkeypatch.setenv("AUTH_ALLOW_SIGNUP", "1")
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
    monkeypatch.setenv("AUTH_ALLOW_SIGNUP", "1")
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


# ---- messages -------------------------------------------------------------------------------------------------
def test_messages_reach_the_people_addressed_and_read_state_is_per_person(on):
    admin_pw = "master-secret-9"
    import os
    from pathlib import Path
    f = Path(os.environ.get("TMPDIR", "/tmp")) / "msg-master-pw"
    f.write_text(admin_pw)
    os.environ["AUTH_MASTER_PASSWORD_FILE"] = str(f)
    core._master_mtime = 0.0
    assert login(on, "admin", admin_pw).status_code == 200
    r = on.post("/messages", json={"to_kind": "role", "to_value": "pilot", "subject": "Gate change", "body": "Your departure moved to gate B4."})
    assert r.status_code == 200 and r.json()["recipients"] == 1 and r.json()["read_by"] == 0
    msg_id = r.json()["id"]
    assert on.post("/messages", json={"to_kind": "all", "subject": "Hello", "body": "Everyone"}).status_code == 200  # master may broadcast
    on.post("/auth/logout")
    login(on, "crew")  # the pilot account
    assert on.get("/messages/unread").json()["count"] == 2
    inbox = on.get("/messages").json()
    assert {m["subject"] for m in inbox} >= {"Gate change", "Hello"} and not any(m["read"] for m in inbox if m["subject"] == "Gate change")
    assert on.post(f"/messages/{msg_id}/read").status_code == 200
    assert on.get("/messages/unread").json()["count"] == 1  # the blue badge goes down once it is opened
    on.post("/auth/logout")
    login(on, "dispatch")
    assert on.get("/messages/unread").json()["count"] >= 1  # still unread for someone else
    assert not any(m["subject"] == "Gate change" for m in on.get("/messages").json())  # not addressed to dispatch
    assert on.post("/messages", json={"to_kind": "all", "subject": "x", "body": "y"}).status_code == 403
    assert on.post(f"/messages/{msg_id}/read").status_code == 404


def test_messages_need_a_session(on):
    assert on.get("/messages").status_code == 401
    assert on.post("/messages", json={"to_kind": "role", "to_value": "ops", "subject": "a", "body": "b"}).status_code == 401


# ---- public-hosting safety --------------------------------------------------------------------------------------
def test_signup_off_by_default_and_never_grants_admin(on, monkeypatch):
    base = {"username": "new.user", "password": "longenough1", "role": "dispatch"}
    assert on.post("/auth/signup", json=base).status_code == 403
    monkeypatch.setenv("AUTH_ALLOW_SIGNUP", "1")
    assert on.post("/auth/signup", json={**base, "role": "admin"}).status_code == 403
    assert on.post("/auth/signup", json=base).status_code == 200


def test_demo_password_hidden(on, monkeypatch):
    assert on.get("/auth/config").json()["demo_password"] == "demo-pass-1"
    monkeypatch.setenv("AUTH_SHOW_DEMO", "0")
    cfg = on.get("/auth/config").json()
    assert cfg["demo_password"] is None and cfg["demo_accounts"] == []


def test_changed_demo_password_rehashes_seeded_accounts(on, monkeypatch):
    monkeypatch.setenv("AUTH_DEMO_PASSWORD", "rotated-pass-2")
    core.seed()
    assert login(on, "operations").status_code == 401
    assert login(on, "operations", "rotated-pass-2").status_code == 200


def test_forwarded_for_is_ignored_unless_proxy_trusted(on, monkeypatch):
    for i in range(8):
        r = on.post("/auth/login", json={"username": "operations", "password": "bad"}, headers={"X-Forwarded-For": f"9.9.9.{i}"})
        assert r.status_code == 401
    # same socket peer, different spoofed addresses: still one key, so the ninth try is limited
    r = on.post("/auth/login", json={"username": "operations", "password": "bad"}, headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 429
    assert core.client_ip("10.0.0.1", "1.1.1.1, 2.2.2.2") == "10.0.0.1"
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    assert core.client_ip("10.0.0.1", "6.6.6.6, 2.2.2.2") == "2.2.2.2"


def test_failure_table_is_bounded(monkeypatch):
    monkeypatch.setattr(core, "_fails", {})
    monkeypatch.setattr(core, "_MAX_FAIL_KEYS", 50)
    for i in range(500):
        core.note_failure(f"k{i}")
    assert len(core._fails) <= 50 and "k499" in core._fails
