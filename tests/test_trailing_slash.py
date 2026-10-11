"""A trailing slash must not trigger FastAPI's redirect: behind the proxy its Location is the internal host and the browser's fetch fails."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, follow_redirects=False)


def test_trailing_slash_is_served_not_redirected():
    for path in ("/plan/", "/fleet/", "/health/"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code, r.headers.get("location"))
        assert "location" not in r.headers
