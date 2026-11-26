"""Admin route for an agent's posture score."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.db.models import AdminKey
from tessera_server.services.posture import agent_posture

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)


def posture_router() -> APIRouter:
    """Build the posture route.

    Returns:
        A router with `GET /v1/agents/{id}/posture`.

    Example:
        ```python
        router = posture_router()
        assert router is not None
        ```
    """
    router = APIRouter(tags=["posture"])

    @router.get("/v1/agents/{agent_id}/posture")
    def read_posture(
        agent_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """Return the posture score for one agent.

        Args:
            agent_id: Agent to score.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            `score`, `coverage`, and `components` for the last seven days.
        """
        return agent_posture(session, agent_id)

    return router
