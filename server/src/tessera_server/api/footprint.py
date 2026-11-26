"""Admin footprint totals."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.api.errors import ApiError
from tessera_server.db.models import AdminKey
from tessera_server.services.footprint import access_footprint, footprint_totals

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)


def footprint_router() -> APIRouter:
    """Build the footprint route.

    Returns:
        A router with `GET /v1/footprint`.

    Example:
        ```python
        router = footprint_router()
        assert router is not None
        ```
    """
    router = APIRouter(tags=["footprint"])

    @router.get("/v1/footprint")
    def read_footprint(
        *,
        agent_id: str | None = None,
        start: str | None = Query(default=None, alias="from"),
        end: str | None = Query(default=None, alias="to"),
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Total tokens, cost, calls, and denials.

        Args:
            agent_id: Agent to total. Omit it to include every agent.
            start: Include events at or after this ISO-8601 time.
            end: Include events at or before this ISO-8601 time.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            `tokens_in`, `tokens_out`, `cost_usd`, `model_calls`,
            `tool_calls`, and `denials`.
        """
        return footprint_totals(
            session,
            agent_id=agent_id,
            start=_moment(start, "from"),
            end=_moment(end, "to"),
        )

    @router.get("/v1/agents/{agent_id}/footprint/access")
    def read_access(
        agent_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Compare declared grants with what the agent used.

        Args:
            agent_id: Agent to report.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            Declared, observed, unused, and denied resources for 30 days.
        """
        return access_footprint(session, agent_id)

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


__all__ = ["footprint_router"]
