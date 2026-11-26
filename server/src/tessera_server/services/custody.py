"""Provider API keys encrypted with the server key-encryption key."""

from __future__ import annotations

import base64
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent_tessera.errors import ConfigError
from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import Agent, Instance, ProviderSecret, utcnow
from tessera_server.ids import new_ulid
from tessera_server.services.audit import append_audit
from tessera_server.services.keys import verify_runtime_token

_NONCE_BYTES = 12
_SECRET_LIMIT = 4096
_DEFAULT_TTL = 60
_MIN_TTL = 1
_MAX_TTL = 120
_ONE = 1
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def create_provider_secret(
    session: Session,
    settings: Settings,
    *,
    name: str,
    provider: str,
    secret: str,
    actor: str,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Store one provider key. The plaintext is not returned.

    Args:
        session: Open database session. The row is committed.
        settings: Key-encryption key.
        name: Unique label.
        provider: Provider the key belongs to.
        secret: Plaintext key. It is encrypted before it is stored.
        actor: Audit actor.
        now: Clock for the row timestamps.

    Returns:
        The id, name, and provider.

    Raises:
        ApiError: Status 422 when a field is not valid, or 409 when the name
            is already stored.
    """
    _check_label(name, "name", _NAME)
    _check_label(provider, "provider", _PROVIDER)
    _check_secret(secret)
    moment = (now or utcnow)()
    row = ProviderSecret(
        id=new_ulid(),
        name=name,
        provider=provider,
        ciphertext="",
        created_at=moment,
        updated_at=moment,
    )
    row.ciphertext = _seal(settings.kek, row.id, secret)
    _insert(session, row)
    append_audit(
        session,
        actor=actor,
        entry_type="provider_secret_created",
        payload={"id": row.id, "name": row.name, "provider": row.provider},
        now=now,
    )
    return _public(row)


def list_provider_secrets(session: Session) -> dict[str, object]:
    """List stored keys by name, without plaintext or ciphertext.

    Args:
        session: Open database session.

    Returns:
        `items` of id, name, and provider, ordered by name.
    """
    rows = session.scalars(select(ProviderSecret).order_by(ProviderSecret.name)).all()
    return {"items": [_public(row) for row in rows]}


def get_provider_secret(session: Session, secret_id: str) -> dict[str, object]:
    """Return one stored key's id, name, and provider.

    Args:
        session: Open database session.
        secret_id: Row to read.

    Returns:
        The public fields.

    Raises:
        ApiError: Status 404 when the row is missing.
    """
    return _public(_require(session, secret_id))


def update_provider_secret(
    session: Session,
    settings: Settings,
    secret_id: str,
    *,
    name: str | None,
    provider: str | None,
    secret: str | None,
    actor: str,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Replace the label, provider, or plaintext of a stored key.

    Args:
        session: Open database session. The row is committed.
        settings: Key-encryption key, used when `secret` is set.
        secret_id: Row to update.
        name: New label. `None` leaves the current name.
        provider: New provider. `None` leaves the current provider.
        secret: New plaintext. `None` leaves the ciphertext unchanged.
        actor: Audit actor.
        now: Clock for `updated_at`.

    Returns:
        The public fields. The plaintext is not included.

    Raises:
        ApiError: Status 404 when the row is missing, 422 when a field is not
            valid or no field was sent, or 409 when the new name is taken.
    """
    if name is None and provider is None and secret is None:
        raise ApiError(422, "invalid_request", "nothing to update")
    row = _require(session, secret_id)
    if name is not None:
        _check_label(name, "name", _NAME)
        row.name = name
    if provider is not None:
        _check_label(provider, "provider", _PROVIDER)
        row.provider = provider
    if secret is not None:
        _check_secret(secret)
        row.ciphertext = _seal(settings.kek, row.id, secret)
    row.updated_at = (now or utcnow)()
    _flush(session, "a provider secret with this name already exists")
    append_audit(
        session,
        actor=actor,
        entry_type="provider_secret_updated",
        payload={"id": row.id, "name": row.name, "provider": row.provider},
        now=now,
    )
    return _public(row)


def delete_provider_secret(
    session: Session,
    secret_id: str,
    *,
    actor: str,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Delete a stored key.

    Args:
        session: Open database session. The delete is committed.
        secret_id: Row to delete.
        actor: Audit actor.
        now: Clock for the audit row.

    Returns:
        The deleted id.

    Raises:
        ApiError: Status 404 when the row is missing.
    """
    row = _require(session, secret_id)
    session.delete(row)
    append_audit(
        session,
        actor=actor,
        entry_type="provider_secret_deleted",
        payload={"id": secret_id, "name": row.name, "provider": row.provider},
        now=now,
    )
    return {"id": secret_id}


def issue_provider_secret(
    session: Session,
    settings: Settings,
    token: str,
    *,
    name: str | None,
    provider: str | None,
    ttl: int | None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, str]:
    """Give a running instance one provider key for a short time.

    Args:
        session: Open database session. The audit row is committed.
        settings: Issuer and key-encryption key.
        token: Runtime JWT for the instance that will use the key.
        name: Stored label. `None` selects by provider.
        provider: Provider to select. `None` uses the named row's provider.
        ttl: Lifetime in seconds. `None` uses 60. Values above 120 are rejected.
        now: Clock for expiry and the audit row.

    Returns:
        `provider`, `api_key`, and `expires_at`. The audit row does not include
        the key.

    Raises:
        ApiError: Status 401 when the token is not a runtime JWT, 403 when the
            agent is not active or the instance is revoked, 404 when no key
            matches, 409 when a provider matches more than one key, or 422
            when the request is not valid.
        ConfigError: When the key-encryption key cannot open the ciphertext.
    """
    moment = (now or utcnow)()
    claims = _runtime_claims(session, settings, token, moment)
    _require_running(session, claims)
    if ttl is None:
        seconds = _DEFAULT_TTL
    elif isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < _MIN_TTL or ttl > _MAX_TTL:
        raise ApiError(422, "invalid_request", "ttl must be from 1 to 120")
    else:
        seconds = ttl
    row = _select_secret(session, name, provider)
    api_key = decrypt_provider_secret(row, settings)
    expires = moment + timedelta(seconds=seconds)
    stamp = expires.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    append_audit(
        session,
        actor=claims["sub"],
        entry_type="provider_secret_issued",
        payload={
            "id": row.id,
            "name": row.name,
            "provider": row.provider,
            "agent_id": claims["agent_id"],
            "instance_id": claims["instance_id"],
            "expires_at": stamp,
        },
        now=lambda: moment,
    )
    return {"provider": row.provider, "api_key": api_key, "expires_at": stamp}


def decrypt_provider_secret(row: ProviderSecret, settings: Settings) -> str:
    """Decrypt a stored provider key.

    Args:
        row: Stored key.
        settings: Key-encryption key that sealed it.

    Returns:
        The plaintext key.

    Raises:
        ConfigError: When the key-encryption key cannot open the ciphertext.
    """
    try:
        blob = base64.b64decode(row.ciphertext, validate=True)
        nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        raw = AESGCM(settings.kek).decrypt(nonce, ciphertext, row.id.encode())
    except (InvalidTag, ValueError, TypeError, UnicodeError) as err:
        raise ConfigError("TESSERA_SERVER_KEK cannot decrypt provider secrets") from err
    return raw.decode()


def _seal(kek: bytes, secret_id: str, secret: str) -> str:
    """Encrypt a provider key for one row.

    Args:
        kek: 32-byte key-encryption key.
        secret_id: Associated data, so the ciphertext cannot move to another row.
        secret: Plaintext key.

    Returns:
        Base64 of the nonce followed by the ciphertext.
    """
    nonce = secrets.token_bytes(_NONCE_BYTES)
    encrypted = AESGCM(kek).encrypt(nonce, secret.encode(), secret_id.encode())
    return base64.b64encode(nonce + encrypted).decode("ascii")


def _public(row: ProviderSecret) -> dict[str, object]:
    """Return the fields a response may include.

    Args:
        row: Stored key.

    Returns:
        Id, name, and provider.
    """
    return {"id": row.id, "name": row.name, "provider": row.provider}


def _require(session: Session, secret_id: str) -> ProviderSecret:
    """Load a stored key or raise not-found.

    Args:
        session: Open database session.
        secret_id: Row to load.

    Returns:
        The stored row.

    Raises:
        ApiError: Status 404 when the row is missing.
    """
    row = session.get(ProviderSecret, secret_id)
    if row is None:
        raise ApiError(404, "not_found", "provider secret is not stored")
    return row


def _insert(session: Session, row: ProviderSecret) -> None:
    """Insert a row or raise a name conflict.

    Args:
        session: Open database session.
        row: Pending key.

    Raises:
        ApiError: Status 409 when the name is already stored.
    """
    try:
        session.add(row)
        session.flush()
    except IntegrityError as err:
        session.rollback()
        raise ApiError(409, "conflict", "a provider secret with this name already exists") from err


def _flush(session: Session, conflict: str) -> None:
    """Flush pending changes or raise a conflict.

    Args:
        session: Open database session.
        conflict: Message used when a uniqueness constraint fails.

    Raises:
        ApiError: Status 409 when the database rejects the change.
    """
    try:
        session.flush()
    except IntegrityError as err:
        session.rollback()
        raise ApiError(409, "conflict", conflict) from err


def _check_label(value: str, field: str, pattern: re.Pattern[str]) -> None:
    """Reject a label that is empty or outside the allowed shape.

    Args:
        value: Submitted label.
        field: Field name used in the error.
        pattern: Allowed shape.

    Raises:
        ApiError: Status 422 when the label does not match.
    """
    if pattern.fullmatch(value) is None:
        raise ApiError(422, "invalid_request", f"{field} is not a valid label")


def _runtime_claims(
    session: Session,
    settings: Settings,
    token: str,
    moment: datetime,
) -> dict[str, str]:
    """Verify a runtime token and read its identity claims.

    Args:
        session: Open database session.
        settings: Expected issuer.
        token: Compact JWT.
        moment: Current time.

    Returns:
        `agent_id`, `instance_id`, and `sub`.

    Raises:
        ApiError: Status 401 when the token or its claims are not usable.
    """
    try:
        claims = verify_runtime_token(session, settings, token, now=lambda: moment)
    except ValueError as err:
        raise ApiError(401, "invalid_token", "runtime token is not valid") from err
    agent_id = claims.get("agent_id")
    instance_id = claims.get("inst")
    subject = claims.get("sub")
    if not isinstance(agent_id, str) or not isinstance(instance_id, str):
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    if not isinstance(subject, str):
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    return {"agent_id": agent_id, "instance_id": instance_id, "sub": subject}


def _require_running(session: Session, claims: dict[str, str]) -> None:
    """Reject a token whose agent is not active or whose instance is revoked.

    Args:
        session: Open database session.
        claims: Verified identity claims.

    Raises:
        ApiError: Status 403 when the agent or instance cannot receive a key.
    """
    agent = session.get(Agent, claims["agent_id"])
    instance = session.get(Instance, claims["instance_id"])
    if agent is None or instance is None or instance.agent_id != agent.id:
        raise ApiError(403, "forbidden", "agent cannot receive a provider key")
    if agent.status != "active":
        raise ApiError(403, "forbidden", "agent cannot receive a provider key")
    if instance.revoked:
        raise ApiError(403, "forbidden", "agent cannot receive a provider key")


def _select_secret(session: Session, name: str | None, provider: str | None) -> ProviderSecret:
    """Find the one stored key a request asked for.

    Args:
        session: Open database session.
        name: Stored label, or `None` to select by provider.
        provider: Provider, or `None` when `name` is set.

    Returns:
        The matching row.

    Raises:
        ApiError: Status 422 when neither field is set, 404 when nothing
            matches, or 409 when a provider matches more than one row.
    """
    if name is None and provider is None:
        raise ApiError(422, "invalid_request", "name or provider is required")
    if name is not None:
        _check_label(name, "name", _NAME)
        row = session.scalar(select(ProviderSecret).where(ProviderSecret.name == name))
        if row is None or (provider is not None and row.provider != provider):
            raise ApiError(404, "not_found", "provider secret is not stored")
        return row
    _check_label(provider or "", "provider", _PROVIDER)
    rows = session.scalars(select(ProviderSecret).where(ProviderSecret.provider == provider)).all()
    if not rows:
        raise ApiError(404, "not_found", "provider secret is not stored")
    if len(rows) != _ONE:
        raise ApiError(409, "conflict", "provider matches more than one secret")
    return rows[0]


def _check_secret(secret: str) -> None:
    """Reject an empty or oversized plaintext.

    Args:
        secret: Submitted key.

    Raises:
        ApiError: Status 422 when the key is empty or longer than 4096 bytes.
    """
    if secret == "":
        raise ApiError(422, "invalid_request", "secret is required")
    if len(secret.encode()) > _SECRET_LIMIT:
        raise ApiError(422, "invalid_request", "secret is too long")


__all__ = [
    "create_provider_secret",
    "decrypt_provider_secret",
    "delete_provider_secret",
    "get_provider_secret",
    "issue_provider_secret",
    "list_provider_secrets",
    "update_provider_secret",
]
