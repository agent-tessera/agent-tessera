"""A client with only a server URL registers against a development server."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from agent_tessera import Tessera
from agent_tessera.types import Action


def test_url_alone_auto_registers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url, stop = _serve(tmp_path)
    monkeypatch.setenv("TESSERA_URL", url)
    monkeypatch.delenv("TESSERA_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("TESSERA_POLICY", raising=False)
    monkeypatch.setenv("TESSERA_CACHE_DIR", str(tmp_path / "cache"))
    try:
        with Tessera("helper") as client:
            assert client.mode == "server"
            assert client.policy is not None
            assert client.check(Action.tool("files.read")).allowed is True
            assert client.check(Action.model("any-model")).allowed is True
    finally:
        stop()


def _serve(tmp_path: Path) -> tuple[str, Callable[[], None]]:
    import uvicorn  # noqa: PLC0415

    from tessera_server.app import create_app  # noqa: PLC0415
    from tessera_server.config import Settings  # noqa: PLC0415
    from tessera_server.db.migrate import upgrade_database  # noqa: PLC0415

    database = tmp_path / "server.db"
    url_path = f"sqlite:///{database}"
    upgrade_database(url_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    url = f"http://127.0.0.1:{port}"
    app = create_app(
        Settings(database_url=url_path, issuer=url, org="default", kek=b"k" * 32, dev=True)
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{url}/healthz", timeout=0.2)
        except httpx.HTTPError:
            time.sleep(0.05)
            continue
        if response.status_code == 200 and response.json().get("dev") is True:
            break
        time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("development server did not start")

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=5)
        app.state.engine.dispose()

    return url, stop
