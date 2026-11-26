"""Admin OpenID Connect login with a mocked identity provider."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from jwt.algorithms import OKPAlgorithm
from sqlalchemy import select

import tessera_server.api.oidc as oidc_api
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.models import AuditEntry
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.secrets import create_admin_key

_ISSUER = "https://idp.example"
_CLIENT = "tessera-admin"
_SERVER = "http://127.0.0.1:8700"
_SECRET = "secret"  # noqa: S105


@pytest.fixture
def oidc_app(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, str]]:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
    finally:
        engine.dispose()
    private = Ed25519PrivateKey.generate()
    jwk = json.loads(OKPAlgorithm.to_jwk(private.public_key()))
    jwk["kid"] = "idp"
    jwk["alg"] = "EdDSA"
    good = _token(private, "user-1", _CLIENT)
    bad = _token(private, "user-1", "other-client")
    member = _token(private, "member", _CLIENT, groups=["tessera-admins"])
    outsider = _token(private, "outsider", _CLIENT, groups=["interns"])
    issued = {"good": good, "bad": bad, "member": member, "outsider": outsider}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=_discovery())
        if path.endswith("/jwks"):
            return httpx.Response(200, json={"keys": [jwk]})
        if path.endswith("/token"):
            body = request.content.decode()
            for code, token in issued.items():
                if f"code={code}" in body:
                    return httpx.Response(200, json={"id_token": token})
            return httpx.Response(400, json={})
        return httpx.Response(404)

    def factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(oidc_api, "open_client", factory)
    settings = Settings(
        database_url=database_url,
        issuer=_SERVER,
        org="default",
        kek=b"k" * 32,
        dev=False,
        oidc_issuer=_ISSUER,
        oidc_client_id=_CLIENT,
        oidc_client_secret=_SECRET,
        oidc_required=False,
    )
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            yield client, admin
    finally:
        downgrade_database(database_url)


@pytest.mark.usefixtures("built_console")
def test_key_login_still_works_when_sso_is_optional(oidc_app: tuple[TestClient, str]) -> None:
    client, admin = oidc_app
    signed = client.post("/login", data={"admin_key": admin})
    assert signed.status_code == 200
    cookie = client.cookies.get("tessera_admin")
    assert cookie is not None
    assert cookie.startswith("tss_")
    assert admin not in cookie
    page = client.get("/v1/me")
    assert page.status_code == 200


def test_sso_callback_sets_the_session_cookie(oidc_app: tuple[TestClient, str]) -> None:
    client, _admin = oidc_app
    client.cookies.clear()
    started = client.get("/oidc/start", follow_redirects=False)
    assert started.status_code == 303
    location = started.headers["location"]
    assert location.startswith(f"{_ISSUER}/authorize?")
    assert f"client_id={_CLIENT}" in location
    state = client.cookies.get("tessera_oidc_state")
    assert state
    assert f"state={state}" in location
    finished = client.get(
        "/oidc/callback",
        params={"code": "good", "state": state},
        follow_redirects=False,
    )
    assert finished.status_code == 303
    assert finished.headers["location"].endswith("/app/overview")
    cookie = client.cookies.get("tessera_admin")
    assert cookie is not None
    assert cookie.startswith("tss_")
    assert "user-1" not in cookie
    page = client.get("/v1/me")
    assert page.status_code == 200
    engine = make_engine(client.app.state.settings.database_url)
    try:
        with session_factory(engine)() as session:
            rows = session.scalars(
                select(AuditEntry).where(AuditEntry.entry_type == "admin_login")
            ).all()
    finally:
        engine.dispose()
    assert len(rows) == 1
    assert rows[0].actor == "oidc:user-1"
    payload = dict(rows[0].payload)
    assert payload.pop("session") == cookie.split("_")[1]
    assert payload == {"issuer": _ISSUER, "sub": "user-1", "method": "oidc"}


def test_login_page_offers_sso(oidc_app: tuple[TestClient, str]) -> None:
    client, _admin = oidc_app
    page = client.get("/login")
    assert page.status_code == 200
    assert 'href="/oidc/start"' in page.text
    assert "Sign in with SSO" in page.text
    assert 'name="admin_key"' in page.text


def test_wrong_admin_group_is_forbidden(oidc_app: tuple[TestClient, str]) -> None:
    client, _admin = oidc_app
    current = client.app.state.settings
    grouped = current.model_copy(update={"oidc_admin_group": "tessera-admins"})
    with TestClient(create_app(grouped)) as gated:
        rejected = _callback(gated, "outsider")
        assert rejected.status_code == 403
        assert "admin group is required" in rejected.text
        assert gated.cookies.get("tessera_admin") is None
        gated.cookies.clear()
        allowed = _callback(gated, "member")
        assert allowed.status_code == 303
        assert str(gated.cookies.get("tessera_admin")).startswith("tss_")
        assert gated.get("/v1/me", follow_redirects=False).status_code == 200


def test_sso_rejects_a_bad_audience(oidc_app: tuple[TestClient, str]) -> None:
    client, _admin = oidc_app
    client.cookies.clear()
    client.get("/oidc/start", follow_redirects=False)
    state = client.cookies.get("tessera_oidc_state")
    rejected = client.get(
        "/oidc/callback",
        params={"code": "bad", "state": state},
        follow_redirects=False,
    )
    assert rejected.status_code == 401
    assert client.cookies.get("tessera_admin") is None


def test_required_mode_rejects_the_admin_key(
    database_url: str,
) -> None:
    upgrade_database(database_url)
    engine = make_engine(database_url)
    try:
        with session_factory(engine)() as session:
            admin = create_admin_key(session, "ops")
    finally:
        engine.dispose()
    settings = Settings(
        database_url=database_url,
        issuer=_SERVER,
        kek=b"k" * 32,
        dev=False,
        oidc_issuer=_ISSUER,
        oidc_client_id=_CLIENT,
        oidc_client_secret=_SECRET,
        oidc_required=True,
    )
    try:
        with TestClient(create_app(settings)) as client:
            denied = client.post("/login", data={"admin_key": admin})
            assert denied.status_code == 401
            assert "admin key login is disabled" in denied.text
            assert client.cookies.get("tessera_admin") is None
            page = client.get("/login")
            assert "Sign in with SSO" in page.text
            assert 'name="admin_key"' not in page.text
    finally:
        downgrade_database(database_url)


def test_sso_start_is_missing_until_configured(database_url: str) -> None:
    upgrade_database(database_url)
    settings = Settings(database_url=database_url, issuer=_SERVER, kek=b"k" * 32, dev=False)
    try:
        with TestClient(create_app(settings)) as client:
            missing = client.get("/oidc/start")
            assert missing.status_code == 404
    finally:
        downgrade_database(database_url)


def test_oidc_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERA_SERVER_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("TESSERA_SERVER_ISSUER", "http://example.test")
    monkeypatch.setenv("TESSERA_SERVER_KEK", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("TESSERA_OIDC_ISSUER", f"{_ISSUER}/")
    monkeypatch.setenv("TESSERA_OIDC_CLIENT_ID", _CLIENT)
    monkeypatch.setenv("TESSERA_OIDC_CLIENT_SECRET", _SECRET)
    monkeypatch.setenv("TESSERA_OIDC_REDIRECT_URL", "https://tessera.example/oidc/callback/")
    monkeypatch.setenv("TESSERA_OIDC_REQUIRED", "1")
    monkeypatch.setenv("TESSERA_OIDC_ADMIN_GROUP", "tessera-admins")
    settings = Settings.from_env()
    assert settings.oidc_issuer == _ISSUER
    assert settings.oidc_client_id == _CLIENT
    assert settings.oidc_client_secret == _SECRET
    assert settings.oidc_redirect_url == "https://tessera.example/oidc/callback"
    assert settings.oidc_required is True
    assert settings.oidc_admin_group == "tessera-admins"


def _callback(client: TestClient, code: str) -> httpx.Response:
    started = client.get("/oidc/start", follow_redirects=False)
    state = client.cookies.get("tessera_oidc_state")
    assert started.status_code == 303
    assert state
    return client.get(
        "/oidc/callback",
        params={"code": code, "state": state},
        follow_redirects=False,
    )


def _token(
    private: Ed25519PrivateKey,
    sub: str,
    audience: str,
    groups: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": _ISSUER,
        "sub": sub,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    if groups is not None:
        claims["groups"] = groups
    token = jwt.encode(claims, private, algorithm="EdDSA", headers={"kid": "idp"})
    return token if isinstance(token, str) else token.decode("ascii")


def _discovery() -> dict[str, str]:
    return {
        "issuer": _ISSUER,
        "authorization_endpoint": f"{_ISSUER}/authorize",
        "token_endpoint": f"{_ISSUER}/token",
        "jwks_uri": f"{_ISSUER}/jwks",
    }
