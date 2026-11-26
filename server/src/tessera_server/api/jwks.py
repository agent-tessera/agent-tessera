"""Public JSON Web Key Set."""

from __future__ import annotations

from fastapi import APIRouter, Request

from tessera_server.db.models import utcnow
from tessera_server.services.keys import published_jwks


def jwks_router() -> APIRouter:
    """Build the unauthenticated JWKS route.

    Returns:
        A router serving `GET /.well-known/jwks.json`.
    """
    router = APIRouter()

    @router.get("/.well-known/jwks.json")
    def jwks(request: Request) -> dict[str, object]:
        """List signing keys that are active or not yet retired.

        Args:
            request: Current request. The app state holds the session factory.

        Returns:
            A JWKS document.
        """
        with request.app.state.sessions() as session:
            return {"keys": published_jwks(session, now=utcnow)}

    return router


__all__ = ["jwks_router"]
