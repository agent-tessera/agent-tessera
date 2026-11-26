"""Agent-to-agent chain helpers: run headers and handoff context."""

from __future__ import annotations

from collections.abc import Mapping

from agent_tessera.context import _normalize_span_id

RUN_HEADER = "Tessera-Run"
"""HTTP header that carries the run (task) id across agent hops."""

PARENT_SPAN_HEADER = "X-Tessera-Parent-Span"
"""HTTP header that carries the caller's span id across agent hops."""


def chain_headers(
    *,
    access_token: str | None,
    run_id: str | None,
    parent_span_id: str | None = None,
) -> dict[str, str]:
    """Build Authorization, Tessera-Run, and parent-span headers.

    Args:
        access_token: Runtime JWT, or `None` when offline / unavailable.
        run_id: Active run id, or `None` when no task is open.
        parent_span_id: Active call span to parent the callee under, or
            `None`. Invalid values are omitted.

    Returns:
        Headers to merge into an HTTP request. Empty when no value is set.

    Example:
        ```python
        from agent_tessera.delegation import chain_headers

        headers = chain_headers(access_token="jwt", run_id="run-1")
        assert headers["Authorization"] == "Bearer jwt"
        assert headers["Tessera-Run"] == "run-1"
        ```
    """
    headers: dict[str, str] = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if run_id:
        headers[RUN_HEADER] = run_id
    span = _normalize_span_id(parent_span_id)
    if span is not None:
        headers[PARENT_SPAN_HEADER] = span
    return headers


def run_id_from_headers(headers: Mapping[str, str]) -> str | None:
    """Read the run id from request headers.

    Args:
        headers: Incoming headers. Lookup is case-insensitive for the run
            header name.

    Returns:
        The run id, or `None` when the header is missing or blank.
    """
    return _header_value(headers, RUN_HEADER)


def parent_span_from_headers(headers: Mapping[str, str]) -> str | None:
    """Read a validated parent span id from request headers.

    Args:
        headers: Incoming headers. Lookup is case-insensitive.

    Returns:
        A 16-hex span id, or `None` when missing or malformed.
    """
    return _normalize_span_id(_header_value(headers, PARENT_SPAN_HEADER))


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    """Return a trimmed header value with a case-insensitive name match.

    Args:
        headers: Incoming headers.
        name: Canonical header name.

    Returns:
        The value, or `None` when missing or blank.
    """
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            text = value.strip()
            return text or None
    return None


__all__ = [
    "PARENT_SPAN_HEADER",
    "RUN_HEADER",
    "chain_headers",
    "parent_span_from_headers",
    "run_id_from_headers",
]
