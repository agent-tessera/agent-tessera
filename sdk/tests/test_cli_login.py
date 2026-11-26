"""CLI login, logout, and status against a live control plane."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from agent_tessera.cli import main
from agent_tessera.credentials import resolve_admin_key, resolve_server, save_profile
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key


@pytest.fixture
def control_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[str, str, Path]]:
    database_url = f"sqlite:///{tmp_path / 'server.db'}"
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
    finally:
        engine.dispose()
    settings = Settings(
        database_url=database_url,
        issuer="http://testserver",
        org="default",
        kek=b"k" * 32,
        dev=False,
    )
    app = create_app(settings)
    creds = tmp_path / "credentials.yaml"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TESSERA_URL", raising=False)
    monkeypatch.delenv("TESSERA_ADMIN_KEY", raising=False)
    monkeypatch.setattr("agent_tessera.credentials.credentials_path", lambda: creds)

    with TestClient(app) as starlette:

        def _handler(request: httpx.Request) -> httpx.Response:
            response = starlette.request(
                request.method,
                request.url.path,
                headers=request.headers,
                params=request.url.params,
                content=request.content,
            )
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=response.content,
                request=request,
            )

        transport = httpx.MockTransport(_handler)
        real_get = httpx.get

        def _get(url: str, **kwargs: object) -> httpx.Response:
            if str(url).startswith("http://testserver"):
                with httpx.Client(transport=transport, base_url="http://testserver") as client:
                    path = str(url).removeprefix("http://testserver") or "/"
                    return client.get(path, **kwargs)  # type: ignore[arg-type]
            return real_get(url, **kwargs)  # type: ignore[misc]

        monkeypatch.setattr("agent_tessera.cli.httpx.get", _get)
        try:
            yield "http://testserver", admin, creds
        finally:
            downgrade_database(database_url)


def test_login_writes_0600_and_bad_key_skips_file(
    control_plane: tuple[str, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server, admin, creds = control_plane
    monkeypatch.setattr("agent_tessera.cli.getpass.getpass", lambda _prompt="": "tsa_bad")
    assert main(["login", "--server", server]) == 1
    assert not creds.exists()
    assert "server error" in capsys.readouterr().err

    monkeypatch.setattr("agent_tessera.cli.getpass.getpass", lambda _prompt="": admin)
    assert main(["login", "--server", server]) == 0
    assert creds.exists()
    assert creds.stat().st_mode & 0o777 == 0o600
    assert "logged in" in capsys.readouterr().out
    assert resolve_server() == server
    assert resolve_admin_key() == admin


def test_env_overrides_credentials_file(
    control_plane: tuple[str, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, admin, creds = control_plane
    save_profile(server=server, admin_key=admin, path=creds)
    monkeypatch.setenv("TESSERA_URL", "http://env-server")
    monkeypatch.setenv("TESSERA_ADMIN_KEY", "tsa_from_env")
    assert resolve_server() == "http://env-server"
    assert resolve_admin_key() == "tsa_from_env"


def test_status_and_logout(
    control_plane: tuple[str, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server, admin, creds = control_plane
    monkeypatch.setattr("agent_tessera.cli.getpass.getpass", lambda _prompt="": admin)
    assert main(["login", "--server", server]) == 0
    capsys.readouterr()
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert f"server:  {server}" in out
    assert "actor:   admin:" in out
    assert "pending: 0" in out
    assert main(["logout"]) == 0
    assert not creds.exists()
