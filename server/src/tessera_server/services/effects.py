"""Store and list the opaque effects recorded for a run."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tessera_server.api.errors import ApiError
from tessera_server.db.models import Run, RunEffect

_REF_LIMIT = 500
_UNDO_LIMIT = 200


def store_effect(
    session: Session,
    event: dict[str, object],
    agent_id: str,
    moment: datetime,
) -> None:
    """Copy an `effect` event into the run ledger.

    Args:
        session: Open database session, inside the ingest transaction.
        event: Prepared event. Identity fields are already rewritten.
        agent_id: Agent the event belongs to.
        moment: Receipt time used when the event has no timestamp.

    Only `ref`, `reversible`, and `undo` are stored. Other fields, including
    any content, are left out of the ledger. An event without a string `ref`
    or a string task is ignored.
    """
    if event.get("type") != "effect":
        return
    ref = event.get("ref")
    task = event.get("task")
    if (
        not isinstance(ref, str)
        or ref == ""
        or len(ref) > _REF_LIMIT
        or not isinstance(task, str)
        or task == ""
    ):
        return
    event_id = str(event["id"])
    if session.get(RunEffect, event_id) is not None:
        return
    undo = event.get("undo")
    named = undo if isinstance(undo, str) and undo and len(undo) <= _UNDO_LIMIT else None
    session.add(
        RunEffect(
            id=event_id,
            run_id=task,
            agent_id=agent_id,
            ref=ref,
            reversible=event.get("reversible") is True,
            undo=named,
            created_at=moment,
        )
    )


def list_run_effects(session: Session, run_id: str) -> dict[str, object]:
    """List the effects stored for one run.

    Args:
        session: Open database session.
        run_id: Run to read.

    Returns:
        `items` of `ref`, `reversible`, `undo`, and `agent_id`, oldest first.

    Raises:
        ApiError: Status 404 when the run does not exist.

    Example:
        ```python
        page = list_run_effects(session, "run-1")
        assert "items" in page
        ```
    """
    if session.get(Run, run_id) is None:
        raise ApiError(404, "not_found", "run not found")
    rows = session.scalars(
        select(RunEffect)
        .where(RunEffect.run_id == run_id)
        .order_by(RunEffect.created_at, RunEffect.id)
    ).all()
    return {
        "items": [
            {
                "ref": row.ref,
                "reversible": row.reversible,
                "undo": row.undo,
                "agent_id": row.agent_id,
            }
            for row in rows
        ]
    }


__all__ = ["list_run_effects", "store_effect"]
