"""Admin keys and the shared secret format."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tessera_server.api.errors import ApiError
from tessera_server.db.models import AdminKey, AdminSession, RegistrationToken, utcnow
from tessera_server.ids import new_ulid


def create_admin_key(
    session: Session,
    name: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> str:
    """Create an admin key and return it once.

    Args:
        session: Open database session. The row is committed.
        name: Label stored with the key.
        now: Clock for `created_at`.

    Returns:
        The full key, `tsa_<id>_<secret>`. Only the hash is stored.

    Raises:
        ApiError: Status 422 when the name is blank.
    """
    label = name.strip()
    if label == "":
        raise ApiError(422, "invalid_request", "admin key name is required")
    key_id = new_ulid()
    secret = secrets.token_urlsafe(32)
    session.add(
        AdminKey(
            id=key_id,
            name=label,
            secret_sha256=_digest(secret),
            created_at=(now or utcnow)(),
            revoked=False,
        )
    )
    session.commit()
    return f"tsa_{key_id}_{secret}"


def list_admin_keys(session: Session) -> dict[str, object]:
    """List admin keys without secrets.

    Args:
        session: Open database session.

    Returns:
        `items` with id, name, created_at, last_used_at, and revoked, newest
        first.
    """
    rows = session.scalars(select(AdminKey).order_by(AdminKey.created_at.desc())).all()
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
                "last_used_at": (
                    None
                    if row.last_used_at is None
                    else row.last_used_at.isoformat().replace("+00:00", "Z")
                ),
                "revoked": row.revoked,
            }
            for row in rows
        ]
    }


def revoke_admin_key(
    session: Session,
    key_id: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Revoke an admin key and end every UI session that used it.

    Args:
        session: Open database session. The rows are committed.
        key_id: Key to revoke.
        now: Clock for session revocation.

    Returns:
        The key id.

    Raises:
        ApiError: Status 404 when the key does not exist.
    """
    row = session.get(AdminKey, key_id)
    if row is None:
        raise ApiError(404, "not_found", "admin key not found")
    moment = (now or utcnow)()
    row.revoked = True
    sessions = session.scalars(
        select(AdminSession).where(
            AdminSession.admin_key_id == key_id,
            AdminSession.revoked_at.is_(None),
        )
    ).all()
    for item in sessions:
        item.revoked_at = moment
    session.commit()
    return {"id": key_id}


def rotate_admin_key(
    session: Session,
    name: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Create a new admin key and revoke every active key with the same name.

    Args:
        session: Open database session. The rows are committed.
        name: Label for the new key and for keys to revoke.
        now: Clock for `created_at` on the new row.

    Returns:
        The new full key (`tsa_<id>_<secret>`) and the ids of keys that were
        revoked. Only hashes are stored.
    """
    previous = session.scalars(
        select(AdminKey).where(AdminKey.name == name, AdminKey.revoked.is_(False))
    ).all()
    previous_ids = tuple(row.id for row in previous)
    for row in previous:
        row.revoked = True
    key_id = new_ulid()
    secret = secrets.token_urlsafe(32)
    session.add(
        AdminKey(
            id=key_id,
            name=name,
            secret_sha256=_digest(secret),
            created_at=(now or utcnow)(),
            revoked=False,
        )
    )
    session.commit()
    return f"tsa_{key_id}_{secret}", previous_ids


def create_registration_token(
    session: Session,
    agent_id: str,
    *,
    reusable: bool,
    expires_at: datetime | None,
    created_by: str,
) -> str:
    """Add a registration token. The caller commits the session.

    Args:
        session: Open database session.
        agent_id: Agent the token can register.
        reusable: Whether the token may bootstrap more than one instance.
        expires_at: When a reusable token stops working. Required when reusable.
        created_by: Actor stored with the row.

    Returns:
        The full token, `tsr_<id>_<secret>`. Only the hash is stored.

    Raises:
        ValueError: When a reusable token has no expiry.
    """
    if reusable and expires_at is None:
        raise ValueError("reusable tokens require expires_at")
    key_id = new_ulid()
    secret = secrets.token_urlsafe(32)
    session.add(
        RegistrationToken(
            id=key_id,
            agent_id=agent_id,
            secret_sha256=_digest(secret),
            reusable=reusable,
            expires_at=expires_at,
            created_by=created_by,
            revoked=False,
        )
    )
    return f"tsr_{key_id}_{secret}"


def parse_secret(value: str, *, prefix: str) -> tuple[str, str] | None:
    """Split a `prefix_id_secret` token.

    Args:
        value: Full token.
        prefix: Expected first field, such as `tsa` or `tsr`.

    Returns:
        The id and secret, or `None` when the shape is wrong.
    """
    try:
        kind, key_id, secret = value.split("_", 2)
    except ValueError:
        return None
    if kind != prefix or key_id == "" or secret == "":
        return None
    return key_id, secret


def secret_matches(stored_sha256: str, secret: str) -> bool:
    """Compare a secret with a stored SHA-256 hex digest.

    Args:
        stored_sha256: Hex digest from the database.
        secret: Secret presented by the caller.

    Returns:
        `True` when the digests match.
    """
    return hmac.compare_digest(stored_sha256, _digest(secret))


def secret_digest(secret: str) -> str:
    """Hash a secret the way stored tokens and keys are hashed.

    Args:
        secret: Raw secret.

    Returns:
        The SHA-256 hex digest.
    """
    return _digest(secret)


def _digest(secret: str) -> str:
    """Hash a secret.

    Args:
        secret: Raw secret.

    Returns:
        The SHA-256 hex digest.
    """
    return hashlib.sha256(secret.encode()).hexdigest()


__all__ = [
    "create_admin_key",
    "create_registration_token",
    "list_admin_keys",
    "parse_secret",
    "revoke_admin_key",
    "rotate_admin_key",
    "secret_digest",
    "secret_matches",
]
