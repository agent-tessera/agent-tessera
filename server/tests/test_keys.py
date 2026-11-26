"""Signing keys, JWKS, and runtime JWT helpers."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from agent_tessera.errors import ConfigError
from tessera_server.app import create_app
from tessera_server.config import Settings
from tessera_server.db.migrate import downgrade_database, upgrade_database
from tessera_server.db.session import make_engine, session_factory
from tessera_server.services.keys import (
    ensure_active_key,
    issue_runtime_token,
    load_private_key,
    published_jwks,
    rotate_keys,
    verify_runtime_token,
)

_ISSUER = "http://127.0.0.1:8700"


@pytest.fixture
def settings(database_url: str) -> Iterator[Settings]:
    upgrade_database(database_url)
    try:
        yield Settings(database_url=database_url, issuer=_ISSUER, kek=b"k" * 32, dev=True)
    finally:
        downgrade_database(database_url)


def test_private_key_is_encrypted_and_wrong_kek_fails(settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    try:
        with factory() as session:
            row = ensure_active_key(session, settings)
            private = load_private_key(row, settings)
            raw = private.private_bytes_raw()
            stored = base64.b64decode(row.private_key_enc)
            assert raw not in stored
            assert base64.b64encode(raw).decode() != row.private_key_enc
            again = ensure_active_key(session, settings)
            assert again.kid == row.kid
        with factory() as session:
            row = ensure_active_key(session, settings)
            wrong = settings.model_copy(update={"kek": b"x" * 32})
            with pytest.raises(ConfigError, match="TESSERA_SERVER_KEK"):
                load_private_key(row, wrong)
    finally:
        engine.dispose()


def test_rotation_keeps_the_old_key_until_retire_after(settings: Settings) -> None:
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    try:
        with factory() as session:
            first = ensure_active_key(session, settings, now=lambda: moment)
            first_kid = first.kid
            first_private = load_private_key(first, settings)
            second = rotate_keys(session, settings, now=lambda: moment)
            session.refresh(first)
            assert second.kid != first_kid
            assert first.active is False
            deadline = first.retire_after
            assert deadline is not None
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            assert deadline == moment + timedelta(seconds=settings.bundle_ttl)
            kids = {item["kid"] for item in published_jwks(session, now=lambda: moment)}
            assert kids == {first_kid, second.kid}
            old_token = jwt.encode(
                {
                    "iss": _ISSUER,
                    "aud": "tessera",
                    "iat": int(moment.timestamp()),
                    "exp": int(moment.timestamp()) + 60,
                },
                first_private,
                algorithm="EdDSA",
                headers={"kid": first_kid},
            )
            claims = verify_runtime_token(session, settings, old_token, now=lambda: moment)
            assert claims["aud"] == "tessera"
            first.retire_after = moment - timedelta(seconds=1)
            session.commit()
            visible = {item["kid"] for item in published_jwks(session, now=lambda: moment)}
            assert first_kid not in visible
            with pytest.raises(ValueError, match="unknown kid"):
                verify_runtime_token(session, settings, old_token, now=lambda: moment)
    finally:
        engine.dispose()


def test_runtime_token_rejects_expired_audience_and_unknown_kid(settings: Settings) -> None:
    issued_at = datetime(2026, 1, 1, tzinfo=UTC)
    engine = make_engine(settings.database_url)
    factory = session_factory(engine)
    try:
        with factory() as session:
            token = issue_runtime_token(
                session,
                settings,
                agent_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
                name="bot",
                environment="prod",
                instance_id="a" * 32,
                policy_version=None,
                now=lambda: issued_at,
            )
            verified = verify_runtime_token(session, settings, token, now=lambda: issued_at)
            assert verified["sub"] == "tessera://default/prod/bot"
            assert verified["agent_id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
            assert verified["pver"] is None
            later = issued_at + timedelta(seconds=settings.token_ttl)
            with pytest.raises(ValueError, match="expired"):
                verify_runtime_token(session, settings, token, now=lambda: later)
            header = jwt.get_unverified_header(token)
            payload = jwt.decode(token, options={"verify_signature": False})
            payload["aud"] = "other"
            row = ensure_active_key(session, settings, now=lambda: issued_at)
            resigned = jwt.encode(
                payload,
                load_private_key(row, settings),
                algorithm="EdDSA",
                headers={"kid": header["kid"]},
            )
            with pytest.raises(ValueError, match="wrong audience"):
                verify_runtime_token(session, settings, resigned, now=lambda: issued_at)
            foreign = jwt.encode(
                payload,
                load_private_key(row, settings),
                algorithm="EdDSA",
                headers={"kid": "not-a-real-kid"},
            )
            with pytest.raises(ValueError, match="unknown kid"):
                verify_runtime_token(session, settings, foreign, now=lambda: issued_at)
    finally:
        engine.dispose()


def test_jwks_endpoint_lists_the_active_key(settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            row = ensure_active_key(session, settings)
            kid = row.kid
        app = create_app(settings)
        with TestClient(app) as client:
            response = client.get("/.well-known/jwks.json")
        assert response.status_code == 200
        body = response.json()
        assert body["keys"][0]["kid"] == kid
        assert body["keys"][0]["kty"] == "OKP"
    finally:
        engine.dispose()
