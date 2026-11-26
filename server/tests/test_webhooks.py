"""Webhook CRUD, signed delivery, and the environment catch-all."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key
from tessera_server.services.webhooks import ensure_catch_all

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
_ACTION = {"kind": "tool", "resource": "files.delete"}
_DECISION = {"effect": "require_approval", "reason": "approval_rule"}
_SECRET = "hook-secret"  # noqa: S105


class _Box:
    """Requests received by the local webhook server."""

    def __init__(self) -> None:
        self.hits: list[tuple[str | None, bytes]] = []
        self.codes: list[int] = []


class _Handler(BaseHTTPRequestHandler):
    """Record a POST and answer with the next queued status."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        box: _Box = self.server.box  # type: ignore[attr-defined]
        box.hits.append((self.headers.get("X-Tessera-Signature"), body))
        code = box.codes.pop(0) if box.codes else 204
        self.send_response(code)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args


class _Server(ThreadingHTTPServer):
    """HTTP server that stores the request box."""

    def __init__(self, box: _Box) -> None:
        self.box = box
        super().__init__(("127.0.0.1", 0), _Handler)


@pytest.fixture
def receiver() -> Iterator[_Server]:
    box = _Box()
    server = _Server(box)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.fixture
def api(database_url: str) -> Iterator[tuple[TestClient, dict[str, str], Settings]]:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
    finally:
        engine.dispose()
    settings = Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        org="default",
        kek=b"k" * 32,
        dev=False,
        webhook_secret=_SECRET,
    )
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {admin}"}
    try:
        with TestClient(app) as client:
            yield client, headers, settings
    finally:
        downgrade_database(database_url)


def test_crud_hides_the_secret(api: tuple[TestClient, dict[str, str], Settings]) -> None:
    client, headers, _ = api
    created = client.post(
        "/v1/webhooks",
        headers=headers,
        json={
            "url": "https://hooks.example/tessera",
            "events": ["approval.requested"],
            "secret": "s3",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["has_secret"] is True
    assert "secret" not in body
    assert body["events"] == ["approval.requested"]
    listed = client.get("/v1/webhooks", headers=headers)
    assert listed.json()["items"][0]["id"] == body["id"]
    updated = client.put(
        f"/v1/webhooks/{body['id']}",
        headers=headers,
        json={"active": False, "clear_secret": True},
    )
    assert updated.status_code == 200
    assert updated.json()["active"] is False
    assert updated.json()["has_secret"] is False
    removed = client.delete(f"/v1/webhooks/{body['id']}", headers=headers)
    assert removed.status_code == 200
    assert client.get(f"/v1/webhooks/{body['id']}", headers=headers).status_code == 404


def test_signed_delivery(
    api: tuple[TestClient, dict[str, str], Settings], receiver: _Server
) -> None:
    client, headers, _ = api
    url = _url(receiver)
    stored = client.post(
        "/v1/webhooks",
        headers=headers,
        json={"url": url, "events": [], "secret": _SECRET},
    )
    assert stored.status_code == 200, stored.text
    _approval(client, headers)
    assert len(receiver.box.hits) == 1
    signature, body = receiver.box.hits[0]
    assert signature == "sha256=" + hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    payload = json.loads(body)
    assert payload["event"] == "approval.requested"
    assert payload["status"] == "pending"


def test_retry_then_success(
    api: tuple[TestClient, dict[str, str], Settings], receiver: _Server
) -> None:
    client, headers, _ = api
    receiver.box.codes = [500, 204]
    stored = client.post(
        "/v1/webhooks",
        headers=headers,
        json={"url": _url(receiver), "secret": _SECRET},
    )
    assert stored.status_code == 200, stored.text
    _approval(client, headers)
    assert len(receiver.box.hits) == 2
    logged = client.get("/v1/audit/log", headers=headers)
    assert "webhook_failed" not in logged.text


def test_failure_is_audited(api: tuple[TestClient, dict[str, str], Settings]) -> None:
    client, headers, _ = api
    stored = client.post(
        "/v1/webhooks",
        headers=headers,
        json={"url": "http://127.0.0.1:9/hook", "secret": _SECRET},
    )
    assert stored.status_code == 200, stored.text
    _approval(client, headers)
    logged = client.get("/v1/audit/log", headers=headers)
    types = [item["type"] for item in logged.json()["items"]]
    assert "webhook_failed" in types


def test_filtered_endpoint_skips_approvals(
    api: tuple[TestClient, dict[str, str], Settings], receiver: _Server
) -> None:
    client, headers, _ = api
    stored = client.post(
        "/v1/webhooks",
        headers=headers,
        json={"url": _url(receiver), "events": ["quota.warning"], "secret": _SECRET},
    )
    assert stored.status_code == 200, stored.text
    _approval(client, headers)
    assert receiver.box.hits == []


def test_redirect_is_not_followed(
    api: tuple[TestClient, dict[str, str], Settings], receiver: _Server
) -> None:
    client, headers, _ = api
    receiver.box.codes = [302, 302, 302]
    stored = client.post(
        "/v1/webhooks",
        headers=headers,
        json={"url": _url(receiver), "secret": _SECRET},
    )
    assert stored.status_code == 200, stored.text
    _approval(client, headers)
    assert len(receiver.box.hits) == 3


def test_env_catch_all_uses_the_server_secret(database_url: str, receiver: _Server) -> None:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
    finally:
        engine.dispose()
    settings = Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        org="default",
        kek=b"k" * 32,
        dev=False,
        webhook_url=_url(receiver),
        webhook_secret=_SECRET,
    )
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {admin}"}
    try:
        with TestClient(app) as client:
            listed = client.get("/v1/webhooks", headers=headers)
            assert listed.json()["items"][0]["url"] == _url(receiver)
            assert listed.json()["items"][0]["events"] == []
            assert listed.json()["items"][0]["has_secret"] is False
            engine = make_engine(database_url)
            try:
                with session_factory(engine)() as session:
                    ensure_catch_all(session, settings)
            finally:
                engine.dispose()
            again = client.get("/v1/webhooks", headers=headers)
            assert len(again.json()["items"]) == 1
            _approval(client, headers)
    finally:
        downgrade_database(database_url)
    assert len(receiver.box.hits) == 1
    signature, body = receiver.box.hits[0]
    assert signature == "sha256=" + hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_invalid_url_is_rejected(api: tuple[TestClient, dict[str, str], Settings]) -> None:
    client, headers, _ = api
    response = client.post("/v1/webhooks", headers=headers, json={"url": "file:///tmp/hook"})
    assert response.status_code == 422


def test_ui_send_test_delivers_signed_ping(
    api: tuple[TestClient, dict[str, str], Settings],
    receiver: _Server,
) -> None:
    """Send test posts one signed webhook.ping and never renders the secret."""
    client, headers, _ = api
    secret = "ui-hook-secret"  # noqa: S105
    stored = client.post(
        "/v1/webhooks",
        headers=headers,
        json={
            "url": _url(receiver),
            "events": ["approval.requested"],
            "secret": secret,
        },
    )
    assert stored.status_code == 200, stored.text
    webhook_id = str(stored.json()["id"])
    listed = client.get("/v1/webhooks", headers=headers)
    assert listed.status_code == 200, listed.text
    assert secret not in listed.text
    pinged = client.post(f"/v1/webhooks/{webhook_id}:ping", headers=headers)
    assert pinged.status_code == 200, pinged.text
    assert secret not in pinged.text
    assert len(receiver.box.hits) == 1
    signature, body = receiver.box.hits[0]
    assert signature == "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    payload = json.loads(body)
    assert payload["event"] == "webhook.ping"
    assert payload["webhook_id"] == webhook_id


def _url(server: _Server) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}/hook"


def _approval(client: TestClient, headers: dict[str, str]) -> None:
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
            "supervisors": [],
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
    assert client.post(f"/v1/agents/{agent_id}:activate", headers=headers).status_code == 200
    token_row = client.post(f"/v1/agents/{agent_id}/registration-tokens", headers=headers, json={})
    assert token_row.status_code == 200, token_row.text
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
    assert runtime.status_code == 200, runtime.text
    approval = client.post(
        "/v1/approvals",
        headers={"Authorization": f"Bearer {runtime.json()['access_token']}"},
        json={"action": _ACTION, "decision": _DECISION, "principal": "ada"},
    )
    assert approval.status_code == 200, approval.text
