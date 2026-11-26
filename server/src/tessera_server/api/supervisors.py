"""Admin routes for supervisor grant documents."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.db.models import AdminKey
from tessera_server.services.supervision import (
    acknowledge_supervisor,
    get_supervisor_grants,
    put_supervisor_grants,
)

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)


def supervisors_router() -> APIRouter:
    """Build the supervisor grant routes.

    Returns:
        A router for reading and replacing grant YAML.

    Example:
        ```python
        router = supervisors_router()
        assert router is not None
        ```
    """
    router = APIRouter(tags=["supervisors"])

    @router.put("/v1/supervisors/{supervisor_id}/grants")
    async def put_grants(
        supervisor_id: str,
        request: Request,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, str]:
        """Replace one supervisor's allow lists.

        Args:
            supervisor_id: `user:` or `group:` id.
            request: Request whose body is YAML.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The supervisor id that was stored.
        """
        text = (await request.body()).decode("utf-8")
        put_supervisor_grants(session, supervisor_id, text)
        return {"supervisor": supervisor_id}

    @router.get("/v1/supervisors/{supervisor_id}/grants")
    def read_grants(
        supervisor_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> Response:
        """Return one supervisor's grant YAML.

        Args:
            supervisor_id: `user:` or `group:` id.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The stored YAML document.
        """
        return Response(
            content=get_supervisor_grants(session, supervisor_id),
            media_type="application/yaml",
        )

    @router.post("/v1/agents/{agent_id}/supervisor-ack")
    def ack_agent(
        agent_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, str]:
        """Reset the dead-man timer for one agent.

        Args:
            agent_id: Agent a supervisor is acknowledging.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            The agent id and the acknowledgement time.
        """
        return acknowledge_supervisor(session, agent_id)

    return router


__all__ = ["supervisors_router"]
