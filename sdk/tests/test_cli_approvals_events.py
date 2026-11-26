"""CLI approvals and events tail against a live control plane."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from agent_tessera.cli import main
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key

_POLICY = """\
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: billing
  environment: prod
spec:
  approvals:
    route_to: [principal]
    timeout: 60
"""


@pytest.fixture
def control_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[str, str, TestClient]]:
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

        def _get(url: str, **kwargs: object) -> httpx.Response:
            with httpx.Client(transport=transport, base_url="http://testserver") as client:
                path = str(url).removeprefix("http://testserver") or "/"
                return client.get(path, **kwargs)  # type: ignore[arg-type]

        def _post(url: str, **kwargs: object) -> httpx.Response:
            with httpx.Client(transport=transport, base_url="http://testserver") as client:
                path = str(url).removeprefix("http://testserver") or "/"
                return client.post(path, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("agent_tessera.cli.httpx.get", _get)
        monkeypatch.setattr("agent_tessera.cli.httpx.post", _post)
        monkeypatch.setenv("TESSERA_URL", "http://testserver")
        monkeypatch.setenv("TESSERA_ADMIN_KEY", admin)
        try:
            yield "http://testserver", admin, starlette
        finally:
            downgrade_database(database_url)


def test_approvals_approve_and_events_tail_two_batches(
    control_plane: tuple[str, str, TestClient],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Approve via CLI and tail two event batches without duplicates."""
    _server, admin, client = control_plane
    headers = {"Authorization": f"Bearer {admin}"}
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": "ada@example.com", "display_name": "Ada"},
    )
    assert owner.status_code == 200
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={
            "name": "billing",
            "environment": "prod",
            "owner_id": owner.json()["id"],
            "supervisors": [],
        },
    )
    assert created.status_code == 200
    agent_id = str(created.json()["id"])
    stored = client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_POLICY,
    )
    assert stored.status_code == 200
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    token_row = client.post(f"/v1/agents/{agent_id}/registration-tokens", headers=headers, json={})
    assert token_row.status_code == 200
    runtime = client.post(
        "/v1/token",
        json={
            "grant_type": "registration",
            "registration_token": token_row.json()["token"],
            "instance_id": "ab" * 16,
            "host": "app",
            "runtime": {"sdk": "0.3.0"},
        },
    )
    assert runtime.status_code == 200
    agent_headers = {"Authorization": f"Bearer {runtime.json()['access_token']}"}
    approval = client.post(
        "/v1/approvals",
        headers=agent_headers,
        json={
            "action": {"kind": "tool", "resource": "files.delete"},
            "decision": {"effect": "require_approval", "reason": "approval_rule"},
            "principal": "ada",
        },
    )
    assert approval.status_code == 200, approval.text
    approval_id = str(approval.json()["id"])
    assert main(["approvals", "approve", approval_id, "--comment", "ok"]) == 0
    out = capsys.readouterr().out
    assert approval_id in out
    assert "approved" in out or "allow" in out or "resolved" in out or "approved" in out.lower()
    listed = client.get(f"/v1/approvals/{approval_id}", headers=headers)
    assert listed.json()["status"] == "approved"

    first = client.post(
        "/v1/events",
        headers=agent_headers,
        json={
            "events": [
                {
                    "id": "11" * 16,
                    "ts": "2026-11-05T16:00:00.000Z",
                    "type": "decision",
                    "task": "task-1",
                }
            ]
        },
    )
    assert first.status_code == 200
    assert main(["events", "tail", "--type", "decision"]) == 0
    first_out = capsys.readouterr().out
    assert "11" * 16 in first_out
    second = client.post(
        "/v1/events",
        headers=agent_headers,
        json={
            "events": [
                {
                    "id": "22" * 16,
                    "ts": "2026-11-05T16:01:00.000Z",
                    "type": "decision",
                    "task": "task-1",
                }
            ]
        },
    )
    assert second.status_code == 200
    polls = {"n": 0}

    def _sleep(_seconds: float) -> None:
        polls["n"] += 1
        if polls["n"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("agent_tessera.cli.time.sleep", _sleep)
    assert main(["events", "tail", "--follow", "--type", "decision"]) == 0
    follow_out = capsys.readouterr().out
    assert "11" * 16 in follow_out
    assert "22" * 16 in follow_out
    assert follow_out.count("11" * 16) == 1
    assert follow_out.count("22" * 16) == 1
