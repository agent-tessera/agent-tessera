"""Admin routes for inventory, spend, and denial dashboards."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.api.errors import ApiError
from tessera_server.db.models import AdminKey
from tessera_server.services.dashboards import (
    dashboard_denials,
    dashboard_spend,
    dashboard_summary,
)

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)


def dashboards_router() -> APIRouter:
    """Build the dashboard routes.

    Returns:
        Routes for the summary, spend, and denial documents.

    Example:
        ```python
        router = dashboards_router()
        assert router is not None
        ```
    """
    router = APIRouter(tags=["dashboards"])

    @router.get("/v1/dashboards/summary")
    def read_summary(
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Return inventory and the last 24 hours.

        Args:
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            Inventory, orphaned and dormant counts, pending approvals, spend,
            and denials.
        """
        return dashboard_summary(session)

    @router.get("/v1/dashboards/spend")
    def read_spend(
        *,
        start: str | None = Query(default=None, alias="from"),
        end: str | None = Query(default=None, alias="to"),
        group_by: str = "agent",
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Sum usage cost by agent or model.

        Args:
            start: Include events at or after this ISO-8601 time.
            end: Include events at or before this ISO-8601 time.
            group_by: `agent` or `model`.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            One cost row per group.
        """
        return dashboard_spend(
            session,
            start=_moment(start, "from"),
            end=_moment(end, "to"),
            group_by=group_by,
        )

    @router.get("/v1/dashboards/denials")
    def read_denials(
        *,
        start: str | None = Query(default=None, alias="from"),
        end: str | None = Query(default=None, alias="to"),
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Count denied decisions by agent.

        Args:
            start: Include events at or after this ISO-8601 time.
            end: Include events at or before this ISO-8601 time.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The total and one row per agent.
        """
        return dashboard_denials(
            session,
            start=_moment(start, "from"),
            end=_moment(end, "to"),
        )

    return router


def _moment(value: str | None, name: str) -> datetime | None:
    """Parse an optional ISO-8601 bound.

    Args:
        value: Query value. `None` means the bound is unset.
        name: Query name used in the error.

    Returns:
        An aware datetime, or `None`.

    Raises:
        ApiError: Status 422 when the value is not a timestamp.
    """
    if value is None or value == "":
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ApiError(422, "invalid_request", f"{name} must be a timestamp") from err
    if moment.tzinfo is None:
        raise ApiError(422, "invalid_request", f"{name} must be a timestamp")
    return moment
