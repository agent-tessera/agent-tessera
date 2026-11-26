"""Fixtures shared by SDK tests."""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  tools:
    allow: ["*"]
"""


@dataclass
class LiveServer:
    """A control plane listening on localhost."""

    url: str
    headers: dict[str, str]
    agent_id: str
    token: str
    server: object
    thread: threading.Thread
    app: object
    port: int

    def stop(self) -> None:
        """Stop the server process. Safe to call more than once."""
        if not self.thread.is_alive():
            return
        self.server.should_exit = True
        self.thread.join(timeout=5)

    def restart(self) -> None:
        """Stop the server and bind the same app to the same port."""
        import uvicorn  # noqa: PLC0415

        from tessera_server.db.session import make_engine, session_factory  # noqa: PLC0415

        self.stop()
        previous = getattr(self.app.state, "engine", None)
        if previous is not None:
            previous.dispose()
        engine = make_engine(self.app.state.settings.database_url)
        self.app.state.engine = engine
        self.app.state.sessions = session_factory(engine)
        last_error: Exception | None = None
        for _attempt in range(40):
            time.sleep(0.1)
            server = uvicorn.Server(
                uvicorn.Config(
                    self.app,
                    host="127.0.0.1",
                    port=self.port,
                    log_level="warning",
                    access_log=False,
                )
            )
            server.install_signal_handlers = lambda: None
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            self.server = server
            self.thread = thread
            try:
                _wait_until_up(self.url, timeout=1.0)
            except RuntimeError as err:
                last_error = err
                server.should_exit = True
                thread.join(timeout=2)
                continue
            return
        raise RuntimeError("control plane did not restart") from last_error


@pytest.fixture(autouse=True)
def _spool_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep event spools out of the working tree.

    Args:
        tmp_path: Per-test directory.
        monkeypatch: Environment patcher.
    """
    monkeypatch.setenv("TESSERA_SPOOL_DIR", str(tmp_path / "spool"))


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[LiveServer]:
    """Run the control plane on a free localhost port.

    Uses `TESSERA_TEST_DATABASE_URL` when set (Postgres in CI), otherwise a
    temporary SQLite file.

    Yields:
        The URL, an admin key, and one active agent's registration token.
    """
    import uvicorn  # noqa: PLC0415

    from tessera_server.app import create_app  # noqa: PLC0415
    from tessera_server.config import Settings  # noqa: PLC0415
    from tessera_server.db.migrate import downgrade_database, upgrade_database  # noqa: PLC0415
    from tessera_server.services.secrets import create_admin_key  # noqa: PLC0415

    url_path = os.environ.get("TESSERA_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'server.db'}"
    upgrade_database(url_path)
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    settings = Settings(
        database_url=url_path,
        issuer=url,
        org="default",
        kek=b"k" * 32,
        dev=False,
    )
    app = create_app(settings)
    with app.state.sessions() as session:
        admin = create_admin_key(session, "ops")
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    server.install_signal_handlers = lambda: None
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_up(url)
    headers = {"Authorization": f"Bearer {admin}"}
    live: LiveServer | None = None
    try:
        agent_id, token = _register(url, headers)
        live = LiveServer(url, headers, agent_id, token, server, thread, app, port)
        yield live
    finally:
        # Prefer the live handle: restart() replaces server/thread/engine.
        active = live.server if live is not None else server
        worker = live.thread if live is not None else thread
        active.should_exit = True
        worker.join(timeout=5)
        engine = getattr(app.state, "engine", None)
        if engine is not None:
            engine.dispose()
        downgrade_database(url_path)


def _free_port() -> int:
    """Reserve a localhost port.

    Returns:
        A port that was free when this returned.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_up(url: str, timeout: float = 5) -> None:
    """Block until `/healthz` answers.

    Args:
        url: Server origin.
        timeout: How long to wait, in seconds.

    Raises:
        RuntimeError: If the server does not answer in time.
    """
    import httpx  # noqa: PLC0415

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{url}/healthz", timeout=0.2)
        except httpx.HTTPError:
            time.sleep(0.05)
            continue
        if response.status_code == 200:
            return
        time.sleep(0.05)
    raise RuntimeError("control plane did not start")


def _register(url: str, headers: dict[str, str]) -> tuple[str, str]:
    """Create an active agent and a registration token.

    Args:
        url: Server origin.
        headers: Admin authorization.

    Returns:
        The agent id and the registration token.
    """
    import httpx  # noqa: PLC0415

    with httpx.Client(base_url=url, timeout=5) as client:
        owner = client.post(
            "/v1/owners",
            headers=headers,
            json={"type": "user", "email": "a@example.com", "display_name": "Ada"},
        )
        owner.raise_for_status()
        created = client.post(
            "/v1/agents",
            headers=headers,
            json={"name": "billing", "environment": "prod", "owner_id": owner.json()["id"]},
        )
        created.raise_for_status()
        agent_id = str(created.json()["id"])
        stored = client.put(
            f"/v1/agents/{agent_id}/policy",
            headers={**headers, "Content-Type": "application/yaml"},
            content=_POLICY,
        )
        stored.raise_for_status()
        assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
        token = client.post(f"/v1/agents/{agent_id}/registration-tokens", headers=headers, json={})
        token.raise_for_status()
    return agent_id, str(token.json()["token"])
