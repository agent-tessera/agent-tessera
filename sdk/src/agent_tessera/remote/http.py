"""The only place the SDK constructs an HTTP client."""

from __future__ import annotations

from importlib.metadata import version

import httpx


class RemoteError(Exception):
    """A control-plane call failed before a usable response.

    Args:
        status: HTTP status, or `None` when the server could not be reached.
        message: Short description that does not include credentials.
    """

    def __init__(self, status: int | None, message: str) -> None:
        """Store the status and message.

        Args:
            status: HTTP status, or `None` for a network failure.
            message: Short description.
        """
        self.status = status
        super().__init__(message)


def _new_http_client(base_url: str, timeout: float) -> httpx.Client:
    """Open an HTTP client for one control plane.

    Args:
        base_url: Server origin, without a trailing slash.
        timeout: Connect and read timeout in seconds.

    Returns:
        A client whose user agent names this package.
    """
    return httpx.Client(
        base_url=base_url,
        timeout=timeout,
        headers={"User-Agent": f"agent-tessera/{version('agent-tessera')}"},
    )


def request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    json_body: object | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Send one request and treat network failures and 5xx as remote errors.

    Args:
        client: Open HTTP client.
        method: HTTP method.
        url: Path or URL.
        json_body: JSON request body.
        headers: Extra request headers.

    Returns:
        The response, including 4xx responses.

    Raises:
        RemoteError: If the server cannot be reached or returns 5xx.
    """
    try:
        if json_body is None:
            response = client.request(method, url, headers=headers)
        else:
            response = client.request(method, url, json=json_body, headers=headers)
    except httpx.HTTPError as err:
        raise RemoteError(None, "server is unreachable") from err
    if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
        raise RemoteError(response.status_code, f"server returned {response.status_code}")
    return response


__all__ = ["RemoteError", "_new_http_client", "request"]
