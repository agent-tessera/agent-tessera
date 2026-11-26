"""Fetch a short-lived provider key and place it on a wrapped client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

import httpx

from agent_tessera.errors import ServerError
from agent_tessera.remote.http import RemoteError

_OK = 200


class _Keyed(Protocol):
    """A provider client that stores its credential on `api_key`."""

    api_key: object


class _Sender(Protocol):
    """Control-plane client that can post with the runtime token."""

    def send(self, method: str, path: str, body: object | None = None) -> httpx.Response:
        """Send one authenticated request.

        Args:
            method: HTTP method.
            path: Path on the control plane.
            body: JSON body, or `None`.

        Returns:
            The response.
        """
        ...


@dataclass(frozen=True, slots=True)
class HeldKey:
    """A provider key temporarily written onto a wrapped client.

    Attributes:
        client: Wrapped provider client.
        previous: Value of `api_key` before injection.
    """

    client: object
    previous: object


def issue_provider_key(connection: _Sender, *, provider: str) -> str:
    """Ask the control plane for one provider key.

    Args:
        connection: Open server connection. Its runtime token authenticates
            the request.
        provider: Provider prefix of the model about to be called.

    Returns:
        The plaintext key. The caller discards it after the model call.

    Raises:
        ServerError: When the control plane does not return a key. The error
            text does not include the key.
    """
    try:
        response = connection.send("POST", "/v1/custody/issue", {"provider": provider})
    except RemoteError as err:
        raise ServerError("provider key request failed") from err
    if response.status_code != _OK:
        raise ServerError("provider key request failed")
    return _api_key(response)


def inject_provider_key(clients: list[object], provider: str, api_key: str) -> list[HeldKey]:
    """Set `api_key` on wrapped clients for this provider.

    Args:
        clients: Provider clients passed to `Tessera.wrap`.
        provider: Provider prefix of the model call.
        api_key: Key returned by the control plane.

    Returns:
        The previous `api_key` of each client that was changed.
    """
    held: list[HeldKey] = []
    for client in clients:
        if client_provider(client) != provider or not hasattr(client, "api_key"):
            continue
        keyed = cast("_Keyed", client)
        held.append(HeldKey(client, keyed.api_key))
        keyed.api_key = api_key
    return held


def restore_provider_keys(held: list[HeldKey]) -> None:
    """Put each wrapped client's previous `api_key` back.

    Args:
        held: Clients changed by `inject_provider_key`.
    """
    for item in held:
        cast("_Keyed", item.client).api_key = item.previous
    held.clear()


def client_provider(client: object) -> str | None:
    """Return the resource prefix for a wrapped provider client.

    Args:
        client: Object passed to `Tessera.wrap`.

    Returns:
        A prefix such as `openai` or `anthropic`, or `None` when the client
        is not a known provider.
    """
    module = type(client).__module__
    root = module.split(".")[0]
    if root == "openai":
        return "azure" if "Azure" in type(client).__name__ else "openai"
    if root == "anthropic":
        return "anthropic"
    if root == "google":
        return "gemini"
    if root == "litellm":
        return "litellm"
    if module.startswith("botocore"):
        return "bedrock"
    return None


def _api_key(response: httpx.Response) -> str:
    """Read the key from an issue response.

    Args:
        response: HTTP response from `POST /v1/custody/issue`.

    Returns:
        The plaintext key.

    Raises:
        ServerError: When the body is not a JSON object with a string key.
    """
    try:
        body = response.json()
    except ValueError as err:
        raise ServerError("provider key request failed") from err
    if not isinstance(body, dict):
        raise ServerError("provider key request failed")
    api_key = body.get("api_key")
    if not isinstance(api_key, str) or api_key == "":
        raise ServerError("provider key request failed")
    return api_key


__all__ = [
    "HeldKey",
    "client_provider",
    "inject_provider_key",
    "issue_provider_key",
    "restore_provider_keys",
]
