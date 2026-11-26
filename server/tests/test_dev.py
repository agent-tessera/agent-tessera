"""Development mode: the dev grant and `tessera server dev`."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tessera_server.app import create_app
from tessera_server.cli import main
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Agent, Owner
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.policies import dev_policy_yaml
from tessera_server.services.secrets import create_admin_key

_INSTANCE = "ab" * 16


@pytest.fixture
def dev_api(database_url: str) -> Iterator[tuple[TestClient, TestClient, Settings]]:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            create_admin_key(session, "ops")
    finally:
        engine.dispose()
    settings = Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        org="default",
        kek=b"k" * 32,
        dev=True,
    )
    app = create_app(settings)
    try:
        with (
            TestClient(app) as remote,
            TestClient(app, client=("127.0.0.1", 50000)) as local,
        ):
            yield remote, local, settings
    finally:
        downgrade_database(database_url)


def test_dev_grant_is_loopback_only(database_url: str) -> None:
    upgrade_database(database_url)
    settings = Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        org="default",
        kek=b"k" * 32,
        dev=False,
    )
    app = create_app(settings)
    body = _body()
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            rejected = client.post("/v1/token", json=body)
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "dev_disabled"
    finally:
        downgrade_database(database_url)


def test_dev_grant_registers_and_reuses_the_agent(
    dev_api: tuple[TestClient, TestClient, Settings],
) -> None:
    remote, local, settings = dev_api
    rejected = remote.post("/v1/token", json=_body())
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "dev_disabled"
    incomplete = local.post("/v1/token", json={"grant_type": "dev", "agent": "helper"})
    assert incomplete.status_code == 422
    wrong_env = local.post("/v1/token", json=_body(environment="prod"))
    assert wrong_env.status_code == 422

    first = local.post("/v1/token", json=_body())
    assert first.status_code == 200
    assert first.json()["agent"] == "helper"
    assert first.json()["environment"] == "dev"
    assert first.json()["token_type"] == "Bearer"  # noqa: S105
    agent_id = str(first.json()["agent_id"])
    second = local.post("/v1/token", json=_body(instance_id="cd" * 16))
    assert second.status_code == 200
    assert second.json()["agent_id"] == agent_id
    _assert_draft_is_adopted(remote, local, settings)

    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            owners = session.scalars(select(Owner).where(Owner.email == "dev@localhost")).all()
            assert len(owners) == 1
            agent = session.get(Agent, agent_id)
            assert agent is not None
            assert agent.status == "active"
            assert agent.policy_id is not None
    finally:
        engine.dispose()


def _assert_draft_is_adopted(remote: TestClient, local: TestClient, settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            owner = session.scalars(select(Owner).where(Owner.email == "dev@localhost")).one()
            owner_id = owner.id
    finally:
        engine.dispose()
    headers = {"Authorization": f"Bearer {_admin_key(settings)}"}
    draft = remote.post(
        "/v1/agents",
        headers=headers,
        json={"name": "notes", "environment": "dev", "owner_id": owner_id},
    )
    assert draft.status_code == 200
    adopted = local.post("/v1/token", json=_body(agent="notes", instance_id="ef" * 16))
    assert adopted.status_code == 200
    assert adopted.json()["agent_id"] == draft.json()["id"]


def test_dev_grant_does_not_activate_a_suspended_agent(
    dev_api: tuple[TestClient, TestClient, Settings],
) -> None:
    remote, local, settings = dev_api
    created = local.post("/v1/token", json=_body(agent="paused"))
    assert created.status_code == 200
    agent_id = str(created.json()["agent_id"])
    headers = {"Authorization": f"Bearer {_admin_key(settings)}"}
    assert remote.post(f"/v1/agents/{agent_id}:suspend", headers=headers).status_code == 200
    denied = local.post("/v1/token", json=_body(agent="paused", instance_id="cd" * 16))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "agent_suspended"


def test_dev_policy_matches_the_example() -> None:
    example = Path(__file__).resolve().parents[2] / "policies" / "examples" / "dev-permissive.yaml"
    assert dev_policy_yaml("example") == example.read_text(encoding="utf-8")


def test_dev_command_binds_loopback_and_prints_a_key(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "TESSERA_SERVER_DATABASE_URL",
        "TESSERA_SERVER_ISSUER",
        "TESSERA_SERVER_KEK",
        "TESSERA_SERVER_ORG",
    ):
        monkeypatch.delenv(name, raising=False)
    calls: list[dict[str, object]] = []

    def _run(app, **kwargs: object) -> None:  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        app.state.engine.dispose()
        assert app.state.settings.issuer == "http://127.0.0.1:8701"

    monkeypatch.setattr("tessera_server.cli.uvicorn.run", _run)
    assert main(["dev", "--host", "0.0.0.0"]) == 1  # noqa: S104
    assert "127.0.0.1" in capsys.readouterr().err
    assert calls == []
    assert main(["dev", "--port", "8701"]) == 0
    output = capsys.readouterr().out
    assert "http://127.0.0.1:8701" in output
    assert "tsa_" in output
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8701


def test_dev_banner_is_flushed_before_the_server_starts(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "TESSERA_SERVER_DATABASE_URL",
        "TESSERA_SERVER_ISSUER",
        "TESSERA_SERVER_KEK",
        "TESSERA_SERVER_ORG",
    ):
        monkeypatch.delenv(name, raising=False)
    pipe = _Pipe()
    monkeypatch.setattr(sys, "stdout", pipe)

    def _run(app, **kwargs: object) -> None:  # type: ignore[no-untyped-def]
        app.state.engine.dispose()
        assert pipe.pending == ""
        assert pipe.lines[0] == "http://127.0.0.1:8799\n"
        assert pipe.lines[1].startswith("tsa_")
        assert pipe.lines[1].endswith("\n")
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 8799

    monkeypatch.setattr("tessera_server.cli.uvicorn.run", _run)
    assert main(["dev", "--port", "8799"]) == 0
    assert len(pipe.lines) == 2


class _Pipe:
    """A stdout stand-in that publishes text only when flushed."""

    def __init__(self) -> None:
        """Start with nothing written and nothing flushed."""
        self.pending = ""
        self.lines: list[str] = []

    def write(self, text: str) -> int:
        """Hold text until the next flush.

        Args:
            text: Bytes already decoded by the caller.

        Returns:
            The number of characters accepted.
        """
        self.pending += text
        return len(text)

    def flush(self) -> None:
        """Publish the text held since the previous flush."""
        if self.pending:
            self.lines.append(self.pending)
            self.pending = ""


def _body(
    *,
    agent: str = "helper",
    environment: str = "dev",
    instance_id: str = _INSTANCE,
) -> dict[str, object]:
    return {
        "grant_type": "dev",
        "agent": agent,
        "environment": environment,
        "instance_id": instance_id,
        "host": "laptop",
        "runtime": {"sdk": "0.1.0"},
    }


def _admin_key(settings: Settings) -> str:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            return create_admin_key(session, "again")
    finally:
        engine.dispose()
