"""Console search API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.db.models import AdminKey
from tessera_server.services.search import console_search

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)


def search_router() -> APIRouter:
    """Build the console search route.

    Returns:
        A router with `GET /v1/search`.
    """
    router = APIRouter(tags=["search"])

    @router.get("/v1/search", summary="Search agents and governance entities")
    def read_search(
        *,
        q: Annotated[str, Query()] = "",
        limit: Annotated[int, Query(ge=1, le=20)] = 8,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Search agents, runs, approvals, spans, resources, and rules.

        Args:
            q: Free-text query (min 2 characters).
            limit: Max hits per type (1-20, default 8).
            _admin: Authenticated admin.
            session: Database session.

        Returns:
            `results` for the command palette. Never searches payloads.
        """
        return console_search(session, q, limit=limit)

    return router


__all__ = ["search_router"]
