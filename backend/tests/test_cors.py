from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def preflight(origin: str):
    with TestClient(app) as c:
        return c.options("/api/board", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})


def test_production_site_may_call_the_api():
    for origin in ("https://www.saarathi.tech", "https://saarathi.tech", "http://localhost:5173"):
        r = preflight(origin)
        assert r.status_code == 200, origin
        assert r.headers["access-control-allow-origin"] == origin


def test_unknown_sites_are_refused():
    r = preflight("https://evil.example.com")
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


def test_frontend_origin_accepts_a_comma_separated_list(monkeypatch):
    monkeypatch.setattr(Settings, "frontend_origin", "https://preview.vercel.app/, https://other.example.com")
    origins = Settings().cors_origins
    assert origins[:2] == ["https://preview.vercel.app", "https://other.example.com"]
    assert "https://www.saarathi.tech" in origins and "http://localhost:5173" in origins
