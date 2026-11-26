"""Resolve which supervisor is accountable for a run."""

from __future__ import annotations

from collections.abc import Sequence


def resolve_supervisor(
    run: str | None,
    instance: str | None,
    static: Sequence[str] = (),
) -> str | None:
    """Pick the supervisor for one run.

    A run binding wins, then the instance binding, then the first static
    supervisor on the agent.

    Args:
        run: Supervisor bound to the run, or `None`.
        instance: Supervisor bound to the instance, or `None`.
        static: Supervisors declared on the agent. Empty when none are.

    Returns:
        The chosen supervisor, or `None` when every source is empty.

    Example:
        ```python
        from agent_tessera.supervision import resolve_supervisor

        chosen = resolve_supervisor("user:run", "user:instance", ["user:static"])
        assert chosen == "user:run"
        ```
    """
    if run:
        return run
    if instance:
        return instance
    for item in static:
        if item:
            return item
    return None


__all__ = ["resolve_supervisor"]
