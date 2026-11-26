"""Extract indexed event columns from a stored payload."""

from __future__ import annotations

from collections.abc import Mapping

_LIMITS: Mapping[str, int] = {
    "run_id": 64,
    "span_id": 16,
    "parent_span_id": 16,
    "kind": 16,
    "resource": 300,
    "effect": 24,
    "status": 16,
    "rule": 200,
}


def index_fields(payload: Mapping[str, object]) -> dict[str, str | None]:
    """Pull searchable fields out of an event payload.

    Args:
        payload: Stored event document (identity fields included).

    Returns:
        Column values for `events`, truncated to column lengths. Missing
        values are `None`. Overlong strings are truncated, never rejected.

    Example:
        ```python
        fields = index_fields({"run": "r1", "kind": "tool", "resource": "files.read"})
        assert fields["run_id"] == "r1"
        assert fields["kind"] == "tool"
        ```
    """
    action = payload.get("action")
    action_map = action if isinstance(action, Mapping) else {}
    run = _string(payload.get("run"))
    if run is None:
        run = _string(payload.get("task"))
    resource = _string(action_map.get("resource"))
    if resource is None:
        resource = _string(payload.get("resource"))
    kind = _string(action_map.get("kind"))
    if kind is None:
        kind = _string(payload.get("kind"))
    raw = {
        "run_id": run,
        "span_id": _string(payload.get("span_id")),
        "parent_span_id": _string(payload.get("parent_span_id")),
        "kind": kind,
        "resource": resource,
        "effect": _string(payload.get("effect")),
        "status": _string(payload.get("status")),
        "rule": _string(payload.get("rule")),
    }
    return {name: _truncate(value, _LIMITS[name]) for name, value in raw.items()}


def _string(value: object) -> str | None:
    """Return a non-empty string, or `None`.

    Args:
        value: Candidate field.

    Returns:
        The string, or `None` when missing or blank.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _truncate(value: str | None, limit: int) -> str | None:
    """Cut a string to at most `limit` characters.

    Args:
        value: Field value.
        limit: Maximum length.

    Returns:
        The truncated value, or `None`.
    """
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit]


__all__ = ["index_fields"]
