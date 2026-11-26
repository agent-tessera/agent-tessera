"""Admin routes for provider keys held by the server."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import AdminKey
from tessera_server.services.custody import (
    create_provider_secret,
    delete_provider_secret,
    get_provider_secret,
    issue_provider_secret,
    list_provider_secrets,
    update_provider_secret,
)

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)


class _CreateBody(BaseModel):
    """Body of `POST /v1/custody/secrets`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    provider: str
    secret: str


class _IssueBody(BaseModel):
    """Body of `POST /v1/custody/issue`."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    provider: str | None = None
    ttl: int | None = None


class _UpdateBody(BaseModel):
    """Body of `PUT /v1/custody/secrets/{id}`."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    provider: str | None = None
    secret: str | None = None


def custody_router() -> APIRouter:
    """Build the provider-key admin routes.

    Returns:
        Routes that store, list, update, and delete encrypted keys.

    Example:
        ```python
        router = custody_router()
        assert router is not None
        ```
    """
    router = APIRouter(tags=["custody"])

    @router.post("/v1/custody/secrets")
    def post_secret(
        body: _CreateBody,
        request: Request,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Store a provider key.

        Args:
            body: Name, provider, and plaintext.
            request: Current request. Its settings hold the encryption key.
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The id, name, and provider. The plaintext is not included.
        """
        return create_provider_secret(
            session,
            _settings(request),
            name=body.name,
            provider=body.provider,
            secret=body.secret,
            actor=_actor(admin),
        )

    @router.post("/v1/custody/issue")
    def post_issue(
        body: _IssueBody,
        request: Request,
        session: Session = _SESSION,
    ) -> dict[str, str]:
        """Issue one stored key to the calling instance.

        Args:
            body: Name or provider, and an optional lifetime in seconds.
            request: Current request. The bearer token is a runtime JWT.
            session: Database session.

        Returns:
            The provider, plaintext key, and expiry.

        Raises:
            ApiError: Status 401 when the bearer token is missing.
        """
        return issue_provider_secret(
            session,
            _settings(request),
            _runtime_token(request),
            name=body.name,
            provider=body.provider,
            ttl=body.ttl,
        )

    @router.get("/v1/custody/secrets")
    def read_secrets(
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List stored provider keys.

        Args:
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            Each key's id, name, and provider, ordered by name.
        """
        return list_provider_secrets(session)

    @router.get("/v1/custody/secrets/{secret_id}")
    def read_secret(
        secret_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Return one stored provider key's public fields.

        Args:
            secret_id: Row to read.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The id, name, and provider.
        """
        return get_provider_secret(session, secret_id)

    @router.put("/v1/custody/secrets/{secret_id}")
    def put_secret(
        secret_id: str,
        body: _UpdateBody,
        request: Request,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Replace a stored key's label, provider, or plaintext.

        Args:
            secret_id: Row to update.
            body: Fields to replace. Omitted fields stay as they are.
            request: Current request. Its settings hold the encryption key.
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The id, name, and provider. The plaintext is not included.
        """
        return update_provider_secret(
            session,
            _settings(request),
            secret_id,
            name=body.name,
            provider=body.provider,
            secret=body.secret,
            actor=_actor(admin),
        )

    @router.delete("/v1/custody/secrets/{secret_id}")
    def remove_secret(
        secret_id: str,
        admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Delete a stored provider key.

        Args:
            secret_id: Row to delete.
            admin: Authenticated admin key.
            session: Database session.

        Returns:
            The deleted id.
        """
        return delete_provider_secret(session, secret_id, actor=_actor(admin))

    return router


def _runtime_token(request: Request) -> str:
    """Read the runtime JWT from the Authorization header.

    Args:
        request: Current HTTP request.

    Returns:
        The bearer token.

    Raises:
        ApiError: Status 401 when the header is missing or not a bearer token.
    """
    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer" or credential == "":
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    return credential


def _settings(request: Request) -> Settings:
    """Read the application settings.

    Args:
        request: Current HTTP request.

    Returns:
        Settings stored on the application.
    """
    return cast("Settings", request.app.state.settings)


def _actor(admin: AdminKey | None) -> str:
    """Name the admin for the audit log.

    Args:
        admin: Authenticated admin key, or `None` in development mode.

    Returns:
        `admin:<id>`, or `dev` when development mode skipped the key.
    """
    if admin is None:
        return "dev"
    return f"admin:{admin.id}"


__all__ = ["custody_router"]
