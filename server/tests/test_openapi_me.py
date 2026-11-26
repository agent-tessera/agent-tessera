"""OpenAPI metadata and `GET /v1/me`."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tessera_server import __version__
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key


@pytest.fixture
def client(database_url: str) -> Iterator[tuple[TestClient, dict[str, str]]]:
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
    )
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {admin}"}
    try:
        with TestClient(app) as test_client:
            yield test_client, headers
    finally:
        downgrade_database(database_url)


def test_openapi_tags_summaries_and_version(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    test_client, _headers = client
    schema = test_client.get("/openapi.json").json()
    assert schema["info"]["version"] == __version__
    for path, methods in schema["paths"].items():
        if not path.startswith("/v1"):
            continue
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            assert operation.get("tags"), f"{method.upper()} {path} missing tags"
            assert operation.get("summary"), f"{method.upper()} {path} missing summary"


def test_me_returns_admin_key_identity(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    test_client, headers = client
    response = test_client.get("/v1/me", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "key"
    assert body["actor"].startswith("admin:")
    assert body["admin_key_id"] == body["actor"].removeprefix("admin:")
    assert body["admin_key_id"] is not None
    assert "trace_url" not in body


def test_me_rejects_missing_credential(
    client: tuple[TestClient, dict[str, str]],
) -> None:
    test_client, _headers = client
    response = test_client.get("/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_me_exposes_trace_url_when_configured(database_url: str) -> None:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
    finally:
        engine.dispose()
    template = "https://tracing.example/t/{trace_id}?s={span_id}"
    settings = Settings(
        database_url=database_url,
        issuer="http://127.0.0.1:8700",
        org="default",
        kek=b"k" * 32,
        dev=False,
        trace_url=template,
    )
    app = create_app(settings)
    try:
        with TestClient(app) as test_client:
            response = test_client.get(
                "/v1/me",
                headers={"Authorization": f"Bearer {admin}"},
            )
            assert response.status_code == 200
            assert response.json()["trace_url"] == template
    finally:
        downgrade_database(database_url)
