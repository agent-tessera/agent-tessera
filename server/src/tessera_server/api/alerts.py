"""Admin alert-rule routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.db.models import AdminKey
from tessera_server.services.alerts import create_alert, delete_alert, list_alerts

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)
_Autonomy = Literal["assistive", "interactive", "supervised", "autonomous"]
_Metric = Literal["tokens_in", "tokens_out", "cost_usd", "model_calls", "tool_calls", "denials"]
_Action = Literal["webhook", "suspend"]


class _Selector(BaseModel):
    """Labels and autonomy a rule applies to."""

    model_config = ConfigDict(extra="forbid")

    labels: dict[str, str] = Field(default_factory=dict)
    autonomy: _Autonomy | None = None


class _CreateBody(BaseModel):
    """Body of `POST /v1/alerts`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    metric: _Metric
    threshold: float = Field(gt=0)
    window: str
    selector: _Selector = Field(default_factory=_Selector)
    action: _Action


def alerts_router() -> APIRouter:
    """Build the alert admin routes.

    Returns:
        A router for creating, listing, and deleting rules.

    Example:
        ```python
        router = alerts_router()
        assert router is not None
        ```
    """
    router = APIRouter(tags=["alerts"])

    @router.post("/v1/alerts")
    def post_alert(
        body: _CreateBody,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Store an alert rule.

        Args:
            body: Name, metric, threshold, window, selector, and action.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The stored rule.
        """
        return create_alert(
            session,
            name=body.name,
            metric=body.metric,
            threshold=body.threshold,
            window=body.window,
            labels=body.selector.labels,
            autonomy=body.selector.autonomy,
            action=body.action,
        )

    @router.get("/v1/alerts")
    def read_alerts(
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List alert rules.

        Args:
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            Every stored rule, oldest first.
        """
        return list_alerts(session)

    @router.delete("/v1/alerts/{alert_id}")
    def remove_alert(
        alert_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, str]:
        """Delete an alert rule.

        Args:
            alert_id: Rule to delete.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The deleted id.
        """
        return delete_alert(session, alert_id)

    return router
