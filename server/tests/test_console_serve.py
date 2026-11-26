"""Serve `/app` console assets."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database


@pytest.fixture
def database_ready(database_url: str) -> Iterator[str]:
    upgrade_database(database_url)
    try:
        yield database_url
    finally:
        downgrade_database(database_url)


def _settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        org="default",
        kek=b"k" * 32,
        dev=False,
    )


def test_root_redirects_to_console(database_ready: str) -> None:
    with TestClient(create_app(_settings(database_ready))) as client:
        response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/app"


def test_not_built_page(
    database_ready: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("tessera_server.web._INDEX", tmp_path / "missing.html")
    monkeypatch.setattr("tessera_server.web._ASSETS", tmp_path / "assets")
    with TestClient(create_app(_settings(database_ready))) as client:
        response = client.get("/app")
    assert response.status_code == 503
    assert "Console not built" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_spa_fallback_and_headers(
    database_ready: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    index = tmp_path / "index.html"
    index.write_text("<!doctype html><title>Tessera</title>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr("tessera_server.web._INDEX", index)
    monkeypatch.setattr("tessera_server.web._ASSETS", assets)
    with TestClient(create_app(_settings(database_ready))) as client:
        page = client.get("/app/agents")
        assert page.status_code == 200
        assert "Tessera" in page.text
        assert page.headers["cache-control"] == "no-store"
        assert "default-src 'self'" in page.headers["content-security-policy"]
        assert page.headers["x-frame-options"] == "DENY"
        assert page.headers["referrer-policy"] == "same-origin"
        asset_response = client.get("/app/assets/app.js")
        assert asset_response.status_code == 200
        assert "immutable" in asset_response.headers["cache-control"]


def test_release_workflow_builds_web_first() -> None:
    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    text = workflow.read_text(encoding="utf-8")
    package = text.split("package:")[1]
    assert "needs: [web]" in package
    assert "npm run build" in package
