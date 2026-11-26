"""Quota-lease and quota snapshot endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.api.errors import ApiError
from tessera_server.db.models import AdminKey
from tessera_server.services.quotas import grant_lease, list_quota_snapshot

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)


class _LeaseBody(BaseModel):
    """Body of `POST /v1/quotas/leases`.

    `metric` and `quota_name` default to the v0.2 daily token lease. `used`
    and `requested` are whole units, except `cost_usd`, which counts
    millionths of a dollar.
    """

    model_config = ConfigDict(extra="forbid")

    lease_id: str | None = None
    used: int = Field(default=0, ge=0)
    metric: str = "tokens"
    quota_name: str = "tokens_per_day"
    resource: str | None = None
    principal: str | None = None
    run_id: str | None = None
    requested: int | None = Field(default=None, ge=0)


def quotas_router() -> APIRouter:
    """Build the lease and snapshot routes.

    Returns:
        A router serving `POST /v1/quotas/leases` and `GET /v1/quotas`.
    """
    router = APIRouter(tags=["quotas"])

    @router.get("/v1/quotas", summary="Quota usage vs ceilings")
    def read_quotas(
        *,
        environment: str | None = None,
        agent_id: str | None = None,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Return current usage vs ceilings for matching agents.

        Args:
            environment: Optional exact environment filter.
            agent_id: Optional single agent filter.
            _admin: Authenticated admin key or session.
            session: Database session.

        Returns:
            `{items: [...]}` of quota snapshot rows.
        """
        return list_quota_snapshot(
            session,
            environment=environment,
            agent_id=agent_id,
        )

    @router.post("/v1/quotas/leases")
    def post_lease(
        body: _LeaseBody, request: Request, session: Session = _SESSION
    ) -> dict[str, object]:
        """Close the previous lease and grant the next slice of a budget.

        Args:
            body: Previous lease, the units consumed under it, and the quota
                to draw from.
            request: Request whose bearer token is a runtime JWT.
            session: Database session.

        Returns:
            The new lease, or an empty grant when the budget is exhausted,
            the quota is not leased on the server, or the policy sets no
            daily token limit.
        """
        return grant_lease(
            session,
            request.app.state.settings,
            _bearer(request),
            lease_id=body.lease_id,
            used=body.used,
            metric=body.metric,
            quota_name=body.quota_name,
            resource=body.resource,
            principal=body.principal,
            run_id=body.run_id,
            requested=body.requested,
        )

    return router


def _bearer(request: Request) -> str:
    """Return the bearer credential.

    Args:
        request: Current HTTP request.

    Returns:
        The credential after `Bearer`.

    Raises:
        ApiError: Status 401 when the header is missing or not a bearer token.
    """
    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer" or credential == "":
        raise ApiError(401, "invalid_token", "runtime token is not valid")
    return credential


__all__ = ["quotas_router"]
