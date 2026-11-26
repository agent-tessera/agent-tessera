"""Admin webhook endpoint routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.db.models import AdminKey
from tessera_server.services.webhooks import (
    create_webhook,
    delete_webhook,
    get_webhook,
    list_webhooks,
    update_webhook,
)

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)


class _CreateBody(BaseModel):
    """Body of `POST /v1/webhooks`."""

    model_config = ConfigDict(extra="forbid")

    url: str
    events: list[str] = Field(default_factory=list)
    secret: str | None = None
    active: bool = True


class _UpdateBody(BaseModel):
    """Body of `PUT /v1/webhooks/{id}`."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    events: list[str] | None = None
    secret: str | None = None
    active: bool | None = None
    clear_secret: bool = False


def webhooks_router() -> APIRouter:
    """Build the webhook admin routes.

    Returns:
        A router for creating, reading, updating, and deleting endpoints.

    Example:
        ```python
        router = webhooks_router()
        assert router is not None
        ```
    """
    router = APIRouter(tags=["webhooks"])

    @router.post("/v1/webhooks")
    def post_webhook(
        body: _CreateBody,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Store a webhook endpoint.

        Args:
            body: URL, event names, optional secret, and active flag.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The endpoint. The secret is not echoed.
        """
        return create_webhook(
            session,
            url=body.url,
            events=body.events,
            secret=body.secret,
            active=body.active,
        )

    @router.get("/v1/webhooks")
    def read_webhooks(
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List webhook endpoints.

        Args:
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            Every stored endpoint, oldest first.
        """
        return list_webhooks(session)

    @router.get("/v1/webhooks/{webhook_id}")
    def read_webhook(
        webhook_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Return one webhook endpoint.

        Args:
            webhook_id: Endpoint to read.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The endpoint document.
        """
        return get_webhook(session, webhook_id)

    @router.put("/v1/webhooks/{webhook_id}")
    def put_webhook(
        webhook_id: str,
        body: _UpdateBody,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Change a webhook endpoint.

        Args:
            webhook_id: Endpoint to change.
            body: Fields to replace. Omitted fields stay as they are.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The updated endpoint.
        """
        return update_webhook(
            session,
            webhook_id,
            url=body.url,
            events=body.events,
            secret=body.secret,
            active=body.active,
            clear_secret=body.clear_secret,
        )

    @router.delete("/v1/webhooks/{webhook_id}")
    def remove_webhook(
        webhook_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Delete a webhook endpoint.

        Args:
            webhook_id: Endpoint to delete.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The deleted id.
        """
        return delete_webhook(session, webhook_id)

    return router


__all__ = ["webhooks_router"]
