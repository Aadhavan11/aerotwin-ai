"""Production wrapper: auth, SPA fallback, API mount, LLM rate limits."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.serve import create_app


@pytest.fixture()
def site(tmp_path: Path) -> Path:
    dist = tmp_path / "site"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>INDEX</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    return dist


def _auth(user: str, pw: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def _client(site: Path, **kw) -> TestClient:
    return TestClient(create_app(site, **kw))


def test_auth_gates_everything_but_health(site: Path) -> None:
    c = _client(site, auth_user="u", auth_pass="p")
    assert c.get("/api/health").status_code == 200
    r = c.get("/api/fleet")
    assert r.status_code == 401 and r.headers["www-authenticate"] == 'Basic realm="AeroTwin"'
    assert c.get("/api/fleet", headers=_auth("u", "bad")).status_code == 401
    assert c.get("/", headers=_auth("u", "bad")).status_code == 401
    assert c.get("/api/fleet", headers=_auth("u", "p")).status_code == 200
    assert c.get("/fleet/SIM-017", headers=_auth("u", "p")).status_code == 200


def test_open_when_auth_unset_or_partial(site: Path) -> None:
    assert _client(site).get("/api/fleet").status_code == 200
    assert _client(site, auth_user="u").get("/api/fleet").status_code == 200


def test_spa_deep_link_and_assets(site: Path) -> None:
    c = _client(site)
    assert "INDEX" in c.get("/fleet/SIM-017").text
    assert "INDEX" in c.get("/").text
    assert c.get("/assets/app.js").text == "console.log(1)"


def test_unknown_api_is_json_404(site: Path) -> None:
    r = _client(site).get("/api/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_path_traversal_does_not_leak(site: Path) -> None:
    c = _client(site)
    for p in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/assets/../../secret.txt"):
        assert "TOPSECRET" not in c.get(p).text


def test_openapi_and_docs(site: Path) -> None:
    c = _client(site)
    assert c.get("/api/openapi.json").status_code == 200
    docs = c.get("/api/docs")
    assert docs.status_code == 200 and "/api/openapi.json" in docs.text


def test_missing_dist_is_503(tmp_path: Path) -> None:
    c = _client(tmp_path / "nope")
    assert c.get("/").status_code == 503
    assert c.get("/api/health").status_code == 200


def test_llm_rate_limit_per_ip(site: Path) -> None:
    c = _client(site, llm_rate_per_min=6)
    codes = [c.post("/api/copilot/chat", json={}).status_code for _ in range(7)]
    assert all(code != 429 for code in codes[:6]) and codes[6] == 429
    other = c.post("/api/copilot/chat", json={}, headers={"X-Forwarded-For": "9.9.9.9"})
    assert other.status_code != 429


def test_llm_daily_cap(site: Path) -> None:
    c = _client(site, llm_rate_per_min=100, llm_daily_cap=2)
    codes = [c.post("/api/agents/run", json={}, headers={"X-Forwarded-For": f"1.1.1.{i}"}).status_code for i in range(3)]
    assert codes[2] == 429 and 429 not in codes[:2]
