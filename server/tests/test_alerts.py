"""Alert rules fire a webhook or suspend the agent."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import Event
from tessera_server.services.alerts import evaluate_alerts
from tessera_server.services.secrets import create_admin_key

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
_SECRET = "hook-secret"  # noqa: S105


class _Box:
    """Requests received by the local webhook server."""

    def __init__(self) -> None:
        self.bodies: list[bytes] = []


class _Handler(BaseHTTPRequestHandler):
    """Record a POST and answer 204."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        box: _Box = self.server.box  # type: ignore[attr-defined]
        box.bodies.append(self.rfile.read(length))
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args


class _Server(ThreadingHTTPServer):
    """HTTP server that stores the request box."""

    def __init__(self, box: _Box) -> None:
        self.box = box
        super().__init__(("127.0.0.1", 0), _Handler)


@pytest.fixture
def api(database_url: str) -> Iterator[tuple[TestClient, dict[str, str], Settings]]:
    upgrade_database(database_url)
    settings = Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        org="default",
        kek=b"k" * 32,
        dev=False,
        webhook_secret=_SECRET,
    )
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            with app.state.sessions() as session:
                admin = create_admin_key(session, "ops")
            headers = {"Authorization": f"Bearer {admin}"}
            yield client, headers, settings
    finally:
        downgrade_database(database_url)


def test_threshold_sends_monitor_alert(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    agent_id = _agent(client, headers, labels={"team": "billing"})
    _usage(client, agent_id, cost=2.5)
    box = _Box()
    server = _Server(box)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        created = client.post(
            "/v1/webhooks",
            headers=headers,
            json={"url": f"http://{host}:{port}/hook", "events": ["monitor.alert"]},
        )
        assert created.status_code == 200, created.text
        rule = client.post(
            "/v1/alerts",
            headers=headers,
            json={
                "name": "spend",
                "metric": "cost_usd",
                "threshold": 1,
                "window": "1h",
                "selector": {"labels": {"team": "other"}},
                "action": "webhook",
            },
        )
        assert rule.status_code == 200, rule.text
        with client.app.state.sessions() as session:  # type: ignore[attr-defined]
            assert evaluate_alerts(session, settings) == 0
        matched = client.post(
            "/v1/alerts",
            headers=headers,
            json={
                "name": "spend-team",
                "metric": "cost_usd",
                "threshold": 1,
                "window": "1h",
                "selector": {"labels": {"team": "billing"}, "autonomy": "interactive"},
                "action": "webhook",
            },
        )
        assert matched.status_code == 200, matched.text
        with client.app.state.sessions() as session:  # type: ignore[attr-defined]
            assert evaluate_alerts(session, settings) == 1
            assert evaluate_alerts(session, settings) == 0
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert len(box.bodies) == 1
    payload = json.loads(box.bodies[0])
    assert payload["event"] == "monitor.alert"
    assert payload["agent_id"] == agent_id
    assert payload["metric"] == "cost_usd"
    listed = client.get("/v1/alerts", headers=headers)
    assert listed.status_code == 200, listed.text
    assert "spend-team" in listed.text
    assert "No alert rules" not in listed.text


def test_suspend_action_suspends_the_agent(
    api: tuple[TestClient, dict[str, str], Settings],
) -> None:
    client, headers, settings = api
    agent_id = _agent(client, headers, labels={})
    _usage(client, agent_id, cost=3)
    box = _Box()
    server = _Server(box)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        hook = client.post(
            "/v1/webhooks",
            headers=headers,
            json={"url": f"http://{host}:{port}/hook", "events": ["monitor.alert"]},
        )
        assert hook.status_code == 200, hook.text
        rule = client.post(
            "/v1/alerts",
            headers=headers,
            json={
                "name": "spend-stop",
                "metric": "cost_usd",
                "threshold": 1,
                "window": "30m",
                "action": "suspend",
            },
        )
        assert rule.status_code == 200, rule.text
        assert client.post("/v1/alerts", headers=headers, json={"name": "nope"}).status_code == 422
        with client.app.state.sessions() as session:  # type: ignore[attr-defined]
            assert evaluate_alerts(session, settings) == 1
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    shown = client.get(f"/v1/agents/{agent_id}", headers=headers)
    assert shown.json()["status"] == "suspended"
    assert json.loads(box.bodies[0])["name"] == "spend-stop"


def _agent(client: TestClient, headers: dict[str, str], *, labels: dict[str, str]) -> str:
    owner = client.post(
        "/v1/owners",
        headers=headers,
        json={"type": "user", "email": "ada@example.com", "display_name": "Ada"},
    )
    assert owner.status_code == 200, owner.text
    created = client.post(
        "/v1/agents",
        headers=headers,
        json={
            "name": "billing",
            "environment": "prod",
            "owner_id": owner.json()["id"],
            "labels": labels,
        },
    )
    assert created.status_code == 200, created.text
    agent_id = str(created.json()["id"])
    stored = client.put(
        f"/v1/agents/{agent_id}/policy",
        headers={**headers, "Content-Type": "application/yaml"},
        content=_POLICY,
    )
    assert stored.status_code == 200, stored.text
    activated = client.post(f"/v1/agents/{agent_id}:activate", headers=headers)
    assert activated.status_code == 200, activated.text
    return agent_id


def _usage(client: TestClient, agent_id: str, *, cost: float) -> None:
    with client.app.state.sessions() as session:  # type: ignore[attr-defined]
        session.add(
            Event(
                id="ab" * 16,
                agent_id=agent_id,
                instance_id="cd" * 16,
                event_type="usage",
                ts=datetime.now(UTC),
                task=None,
                payload={"input_tokens": 0, "output_tokens": 0, "cost_usd": cost},
                received_at=datetime.now(UTC),
            )
        )
        session.commit()
