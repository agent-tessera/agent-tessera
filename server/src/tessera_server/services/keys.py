"""Signing keys, their public JWKS, and runtime JWTs."""

from __future__ import annotations

import base64
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_tessera.crypto import public_jwk, signing_key_id
from agent_tessera.errors import ConfigError
from tessera_server.config import Settings
from tessera_server.db.models import SigningKey, utcnow

_NONCE_BYTES = 12
_AUDIENCE = "tessera"


def ensure_active_key(
    session: Session,
    settings: Settings,
    *,
    now: Callable[[], datetime] | None = None,
) -> SigningKey:
    """Return the active signing key, creating one on first use.

    Args:
        session: Open database session. A new key is committed.
        settings: Settings that hold the key-encryption key.
        now: Clock used to decide whether a key is still published.

    Returns:
        The active signing key row.
    """
    clock = now or utcnow
    current = session.scalar(select(SigningKey).where(SigningKey.active.is_(True)))
    if current is not None and _is_published(current, clock()):
        return current
    if current is not None:
        current.active = False
    created = _insert_key(session, settings, clock())
    session.commit()
    return created


def rotate_keys(
    session: Session,
    settings: Settings,
    *,
    now: Callable[[], datetime] | None = None,
) -> SigningKey:
    """Make a new active key and retire the previous one after the bundle TTL.

    Args:
        session: Open database session. The change is committed.
        settings: Settings that hold the key-encryption key and bundle TTL.
        now: Clock for the retirement deadline.

    Returns:
        The new active key.
    """
    clock = now or utcnow
    moment = clock()
    current = ensure_active_key(session, settings, now=lambda: moment)
    current.active = False
    current.retire_after = moment + timedelta(seconds=settings.bundle_ttl)
    created = _insert_key(session, settings, moment)
    session.commit()
    return created


def published_jwks(
    session: Session,
    *,
    now: Callable[[], datetime] | None = None,
) -> list[dict[str, str]]:
    """List public JWKs for active and not-yet-retired keys.

    Args:
        session: Open database session.
        now: Clock compared with `retire_after`.

    Returns:
        JWK objects clients can use to verify tokens and bundles.
    """
    moment = (now or utcnow)()
    rows = session.scalars(select(SigningKey)).all()
    return [public_jwk(_public_key(row), row.kid) for row in rows if _is_published(row, moment)]


def load_private_key(row: SigningKey, settings: Settings) -> Ed25519PrivateKey:
    """Decrypt a stored signing key.

    Args:
        row: Signing-key row.
        settings: Settings that hold the key-encryption key.

    Returns:
        The Ed25519 private key.

    Raises:
        ConfigError: If `TESSERA_SERVER_KEK` cannot decrypt the row.
    """
    try:
        blob = base64.b64decode(row.private_key_enc, validate=True)
        nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        raw = AESGCM(settings.kek).decrypt(nonce, ciphertext, row.kid.encode())
    except (InvalidTag, ValueError, TypeError) as err:
        raise ConfigError("TESSERA_SERVER_KEK cannot decrypt signing keys") from err
    return Ed25519PrivateKey.from_private_bytes(raw)


def issue_runtime_token(
    session: Session,
    settings: Settings,
    *,
    agent_id: str,
    name: str,
    environment: str,
    instance_id: str,
    policy_version: str | None,
    now: Callable[[], datetime] | None = None,
    subject: str | None = None,
    subject_issuer: str | None = None,
    act: Mapping[str, object] | None = None,
) -> str:
    """Sign a runtime JWT with the active key.

    Args:
        session: Open database session.
        settings: Issuer, organization, token lifetime, and encryption key.
        agent_id: Server id of the agent.
        name: Agent slug.
        environment: Environment slug.
        instance_id: SDK instance id.
        policy_version: Current effective policy version, or `None`.
        now: Clock for `iat` and `exp`.
        subject: Principal `sub`. `None` uses the agent URI.
        subject_issuer: `sub_iss` when the principal comes from another issuer.
        act: Nested actor. `None` stores JSON `null`, the agent acting alone.

    Returns:
        A compact JWT.
    """
    clock = now or utcnow
    moment = clock()
    row = ensure_active_key(session, settings, now=lambda: moment)
    private = load_private_key(row, settings)
    issued = int(moment.timestamp())
    payload: dict[str, object] = {
        "iss": settings.issuer,
        "sub": subject or f"tessera://{settings.org}/{environment}/{name}",
        "aud": _AUDIENCE,
        "iat": issued,
        "exp": issued + settings.token_ttl,
        "jti": uuid4().hex,
        "inst": instance_id,
        "pver": policy_version,
        "act": None if act is None else dict(act),
        "agent_id": agent_id,
    }
    if subject_issuer is not None:
        payload["sub_iss"] = subject_issuer
    token = jwt.encode(payload, private, algorithm="EdDSA", headers={"kid": row.kid})
    return token if isinstance(token, str) else token.decode("ascii")


def verify_runtime_token(
    session: Session,
    settings: Settings,
    token: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> Mapping[str, object]:
    """Verify a runtime JWT against published signing keys.

    Args:
        session: Open database session.
        settings: Expected issuer.
        token: Compact JWT.
        now: Clock for expiry and key retirement.

    Returns:
        The verified claims.

    Raises:
        ValueError: If the key is unknown, the token is expired, or the
            audience or issuer does not match.
    """
    moment = (now or utcnow)()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as err:
        raise ValueError("unknown kid") from err
    kid = header.get("kid")
    row = session.get(SigningKey, kid) if isinstance(kid, str) else None
    if row is None or not _is_published(row, moment):
        raise ValueError("unknown kid")
    public = _public_key(row)
    try:
        claims = jwt.decode(
            token,
            public,
            algorithms=["EdDSA"],
            audience=_AUDIENCE,
            issuer=settings.issuer,
            options={"verify_exp": False},
        )
    except jwt.InvalidAudienceError as err:
        raise ValueError("wrong audience") from err
    except jwt.InvalidIssuerError as err:
        raise ValueError("wrong issuer") from err
    except jwt.InvalidTokenError as err:
        raise ValueError("invalid token") from err
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, int) or int(moment.timestamp()) >= exp:
        raise ValueError("expired")
    return claims


def _insert_key(session: Session, settings: Settings, moment: datetime) -> SigningKey:
    """Insert a new active signing key.

    Args:
        session: Open database session.
        settings: Key-encryption key.
        moment: Creation time.

    Returns:
        The new row, not yet committed.
    """
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    kid = signing_key_id(public)
    raw = private.private_bytes_raw()
    nonce = secrets.token_bytes(_NONCE_BYTES)
    encrypted = AESGCM(settings.kek).encrypt(nonce, raw, kid.encode())
    row = SigningKey(
        kid=kid,
        public_key=base64.b64encode(public.public_bytes_raw()).decode("ascii"),
        private_key_enc=base64.b64encode(nonce + encrypted).decode("ascii"),
        active=True,
        created_at=moment,
    )
    session.add(row)
    return row


def _public_key(row: SigningKey) -> Ed25519PublicKey:
    """Decode the stored public key.

    Args:
        row: Signing-key row.

    Returns:
        The Ed25519 public key.
    """
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(row.public_key))


def _is_published(row: SigningKey, moment: datetime) -> bool:
    """Return whether clients should still trust this key.

    Args:
        row: Signing-key row.
        moment: Current time.

    Returns:
        `True` for the active key and for a rotated key before `retire_after`.
    """
    if row.retire_after is None:
        return row.active
    deadline = row.retire_after
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline > moment


__all__ = [
    "ensure_active_key",
    "issue_runtime_token",
    "load_private_key",
    "published_jwks",
    "rotate_keys",
    "verify_runtime_token",
]
