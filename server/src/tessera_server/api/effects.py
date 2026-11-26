"""Admin read of a run's effect ledger."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tessera_server.api.deps import get_session, require_admin
from tessera_server.db.models import AdminKey
from tessera_server.services.effects import list_run_effects

_ADMIN = Depends(require_admin)
_SESSION = Depends(get_session)


def effects_router() -> APIRouter:
    """Build the run effect route.

    Returns:
        A router with `GET /v1/runs/{id}/effects`.

    Example:
        ```python
        router = effects_router()
        assert router is not None
        ```
    """
    router = APIRouter(tags=["runs"])

    @router.get("/v1/runs/{run_id}/effects")
    def read_effects(
        run_id: str,
        _admin: AdminKey | None = _ADMIN,
        session: Session = _SESSION,
    ) -> dict[str, object]:
        """List the opaque effects recorded for a run.

        Args:
            run_id: Run to read.
            _admin: Authenticated admin key.
            session: Database session.

        Returns:
            `items` with `ref`, `reversible`, `undo`, and `agent_id`.
        """
        return list_run_effects(session, run_id)

    return router


__all__ = ["effects_router"]
