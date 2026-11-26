"""Server-side admin UI sessions.

The browser cookie holds only `tss_<id>_<secret>`. The database stores the
SHA-256 of the secret, the actor, and the lifetimes, so a session can expire
or be revoked without trusting anything the browser sends.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from tessera_server.db.models import AdminKey, AdminSession, utcnow
from tessera_server.ids import new_ulid
from tessera_server.services.secrets import parse_secret, secret_digest, secret_matches

_PREFIX = "tss"
_SECRET_BYTES = 32
_CSRF_BYTES = 32
_TOUCH_SECONDS = 60


@dataclass(frozen=True)
class SignedInAdmin:
    """The admin behind a valid UI session.

    Args:
        session_id: Session row id.
        actor: `admin:<key id>` for an admin key, or `oidc:<sub>` for SSO.
        admin_key: Key the session was created from, or `None` for SSO.
        csrf: Token forms must echo back.
    """

    session_id: str
    actor: str
    admin_key: AdminKey | None
    csrf: str


def create_admin_session(
    session: Session,
    *,
    actor: str,
    admin_key_id: str | None,
    max_age: int,
    now: Callable[[], datetime] | None = None,
) -> tuple[str, str]:
    """Start a UI session and return its cookie value once.

    Args:
        session: Open database session. The row is committed.
        actor: `admin:<key id>` or `oidc:<sub>`.
        admin_key_id: Key the session belongs to, or `None` for SSO.
        max_age: Absolute lifetime in seconds.
        now: Clock for the timestamps.

    Returns:
        The session id and the cookie value, `tss_<id>_<secret>`. Only the hash
        of the secret is stored.
    """
    moment = (now or utcnow)()
    session_id = new_ulid()
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    session.add(
        AdminSession(
            id=session_id,
            secret_sha256=secret_digest(secret),
            actor=actor,
            admin_key_id=admin_key_id,
            csrf=secrets.token_urlsafe(_CSRF_BYTES),
            created_at=moment,
            last_seen_at=moment,
            expires_at=moment + timedelta(seconds=max_age),
            revoked_at=None,
        )
    )
    session.commit()
    return session_id, f"{_PREFIX}_{session_id}_{secret}"


def resolve_admin_session(
    session: Session,
    cookie: str,
    *,
    idle: int,
    now: Callable[[], datetime] | None = None,
) -> SignedInAdmin | None:
    """Look up the admin behind a session cookie.

    A session is valid while it is not revoked, before its absolute expiry,
    within `idle` seconds of its last use, and, for a key session, while that
    key is not revoked. `last_seen_at` moves forward at most once a minute.

    Args:
        session: Open database session. A touched row is committed.
        cookie: Cookie value, `tss_<id>_<secret>`.
        idle: Idle timeout in seconds.
        now: Clock for the checks.

    Returns:
        The signed-in admin, or `None` when the cookie is not a usable session.
        The key row, when present, is detached from `session`.
    """
    parsed = parse_secret(cookie, prefix=_PREFIX)
    if parsed is None:
        return None
    session_id, secret = parsed
    row = session.get(AdminSession, session_id)
    if row is None or not secret_matches(row.secret_sha256, secret):
        return None
    moment = (now or utcnow)()
    if row.revoked_at is not None or _aware(row.expires_at) <= moment:
        return None
    if moment - _aware(row.last_seen_at) >= timedelta(seconds=idle):
        return None
    key: AdminKey | None = None
    if row.admin_key_id is not None:
        key = session.get(AdminKey, row.admin_key_id)
        if key is None or key.revoked:
            return None
    if moment - _aware(row.last_seen_at) >= timedelta(seconds=_TOUCH_SECONDS):
        row.last_seen_at = moment
        if key is not None:
            key.last_used_at = moment
        session.commit()
    if key is not None:
        session.refresh(key)
        session.expunge(key)
    return SignedInAdmin(session_id=row.id, actor=row.actor, admin_key=key, csrf=row.csrf)


def revoke_admin_session(
    session: Session,
    cookie: str,
    *,
    now: Callable[[], datetime] | None = None,
) -> str | None:
    """End the session named by a cookie.

    Args:
        session: Open database session. The row is committed.
        cookie: Cookie value, `tss_<id>_<secret>`.
        now: Clock for `revoked_at`.

    Returns:
        The actor of the session that ended, or `None` when the cookie did not
        name a live session.
    """
    parsed = parse_secret(cookie, prefix=_PREFIX)
    if parsed is None:
        return None
    session_id, secret = parsed
    row = session.get(AdminSession, session_id)
    if row is None or row.revoked_at is not None:
        return None
    if not secret_matches(row.secret_sha256, secret):
        return None
    row.revoked_at = (now or utcnow)()
    session.commit()
    return row.actor


def _aware(moment: datetime) -> datetime:
    """Treat a stored naive timestamp as UTC.

    Args:
        moment: Timestamp read from the database.

    Returns:
        A timezone-aware timestamp.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


__all__ = [
    "SignedInAdmin",
    "create_admin_session",
    "resolve_admin_session",
    "revoke_admin_session",
]
