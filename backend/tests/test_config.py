import pytest

from app.config import Settings


@pytest.mark.parametrize("given", ["postgres://u:p@db.example.com:5432/app", "postgresql://u:p@db.example.com:5432/app"])
def test_hosted_postgres_urls_use_the_installed_psycopg_driver(monkeypatch, given):
    monkeypatch.setenv("DATABASE_URL", given)
    monkeypatch.setattr(Settings, "database_url", given)
    assert Settings().database_url == "postgresql+psycopg://u:p@db.example.com:5432/app"


def test_explicit_driver_and_sqlite_urls_are_left_alone(monkeypatch):
    for url in ("postgresql+psycopg://u:p@h/db", "sqlite:///x.db"):
        monkeypatch.setattr(Settings, "database_url", url)
        assert Settings().database_url == url
