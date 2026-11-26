"""Opt-in detection of provider calls that did not go through an adapter.

The HTTP methods are wrapped only while at least one client asked for
detection. A match records the host and path and then performs the original
call.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import cast
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

_HOSTS = frozenset(
    {
        "api.openai.com",
        "api.anthropic.com",
        "generativelanguage.googleapis.com",
    }
)
_AZURE_SUFFIX = ".openai.azure.com"
_BEDROCK_PREFIX = "bedrock-runtime."
_BEDROCK_SUFFIX = ".amazonaws.com"
_ADAPTER = "agent_tessera.adapters."
_Reporter = Callable[[str, str], None]


class _State:
    """Process-wide patches. Restored when the last reporter detaches."""

    reporters: set[_Reporter]
    httpx_send: Callable[..., object] | None = None
    requests_request: Callable[..., object] | None = None

    def __init__(self) -> None:
        """Start with no reporters and the original methods."""
        self.reporters = set()


_state = _State()


def install(report: _Reporter) -> Callable[[], None]:
    """Start reporting provider calls, and return a function that stops.

    Args:
        report: Called with the host and path of an ungoverned call.

    Returns:
        A function that removes this reporter and restores the originals when
        it was the last one.

    Example:
        ```python
        detach = install(lambda host, path: None)
        detach()
        ```
    """
    _state.reporters.add(report)
    _ensure_httpx()
    _ensure_requests()

    def detach() -> None:
        _state.reporters.discard(report)
        if _state.reporters:
            return
        _restore()

    return detach


def _ensure_httpx() -> None:
    """Wrap `httpx.Client.send` once."""
    if _state.httpx_send is not None:
        return
    import httpx  # noqa: PLC0415

    _state.httpx_send = httpx.Client.send
    setattr(httpx.Client, "send", _httpx_send)  # noqa: B010


def _ensure_requests() -> None:
    """Wrap `requests.Session.request` when requests is installed."""
    if _state.requests_request is not None:
        return
    try:
        import requests  # noqa: PLC0415
    except ImportError:
        return
    _state.requests_request = requests.Session.request
    setattr(requests.Session, "request", _requests_request)  # noqa: B010


def _restore() -> None:
    """Put the original HTTP methods back."""
    if _state.httpx_send is not None:
        import httpx  # noqa: PLC0415

        setattr(httpx.Client, "send", _state.httpx_send)  # noqa: B010
        _state.httpx_send = None
    if _state.requests_request is not None:
        import requests  # noqa: PLC0415

        setattr(requests.Session, "request", _state.requests_request)  # noqa: B010
        _state.requests_request = None


def _httpx_send(self: object, request: object, *args: object, **kwargs: object) -> object:
    """Notice a provider host, then perform the original send.

    Args:
        self: HTTP client.
        request: Outgoing request.
        *args: Extra arguments accepted by the original method.
        **kwargs: Extra keyword arguments accepted by the original method.

    Returns:
        Whatever the original send returns.
    """
    url = getattr(request, "url", None)
    host = getattr(url, "host", None)
    path = getattr(url, "path", None)
    if isinstance(host, str) and isinstance(path, str):
        _notice(host, path)
    send = cast(Callable[..., object], _state.httpx_send)
    return send(self, request, *args, **kwargs)


def _requests_request(
    self: object, method: object, url: object, *args: object, **kwargs: object
) -> object:
    """Notice a provider host, then perform the original request.

    Args:
        self: Requests session.
        method: HTTP method.
        url: URL string.
        *args: Extra arguments accepted by the original method.
        **kwargs: Extra keyword arguments accepted by the original method.

    Returns:
        Whatever the original request returns.
    """
    if isinstance(url, str):
        parts = urlsplit(url)
        if parts.hostname is not None:
            _notice(parts.hostname, parts.path)
    request = cast(Callable[..., object], _state.requests_request)
    return request(self, method, url, *args, **kwargs)


def _notice(host: str, path: str) -> None:
    """Report one provider call that is not inside an adapter.

    Args:
        host: Request host.
        path: Request path, without the query string.
    """
    if not _provider(host) or _from_adapter():
        return
    for report in tuple(_state.reporters):
        try:
            report(host, path)
        except Exception:
            log.exception("bypass detection failed")


def _provider(host: str) -> bool:
    """Return whether a host is a known model provider.

    Args:
        host: Lowercase or mixed-case host.

    Returns:
        `True` for OpenAI, Azure OpenAI, Anthropic, Gemini, and Bedrock.
    """
    name = host.lower()
    if name in _HOSTS or name.endswith(_AZURE_SUFFIX):
        return True
    return name.startswith(_BEDROCK_PREFIX) and name.endswith(_BEDROCK_SUFFIX)


def _from_adapter() -> bool:
    """Return whether an adapter frame is on the stack.

    Returns:
        `True` when a frame's module is under `agent_tessera.adapters`.
    """
    frame = inspect.currentframe()
    while frame is not None:
        module = frame.f_globals.get("__name__")
        if isinstance(module, str) and module.startswith(_ADAPTER):
            return True
        frame = frame.f_back
    return False


__all__ = ["install"]
