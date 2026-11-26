"""Registration and refresh of a runtime credential."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from importlib.metadata import version

import httpx

from agent_tessera.errors import ServerError
from agent_tessera.remote.http import request

_RUNTIME = {"sdk": version("agent-tessera")}


@dataclass(frozen=True)
class RuntimeCredential:
    """A short-lived credential for one instance.

    Attributes:
        access_token: Runtime JWT.
        expires_in: Lifetime in seconds.
        agent_id: Server id of the agent.
        agent: Agent slug.
        environment: Environment slug.
    """

    access_token: str
    expires_in: int
    agent_id: str
    agent: str
    environment: str


def server_is_dev(client: httpx.Client) -> bool:
    """Report whether the server says it is in development mode.

    Args:
        client: Open HTTP client.

    Returns:
        `True` when `/healthz` reports `dev`.

    Raises:
        RemoteError: If the server cannot be reached or returns 5xx.
        ServerError: If the health check is rejected.
    """
    response = request(client, "GET", "/healthz")
    if response.status_code != httpx.codes.OK:
        raise ServerError(f"server returned {response.status_code}")
    body = response.json()
    return bool(isinstance(body, dict) and body.get("dev") is True)


def bootstrap(
    client: httpx.Client,
    *,
    agent: str,
    instance_id: str,
    agent_token: str | None,
    bound_supervisor: str | None = None,
) -> RuntimeCredential:
    """Exchange a registration token, or the dev grant, for a runtime JWT.

    Args:
        client: Open HTTP client.
        agent: Agent slug. Used by the dev grant.
        instance_id: This process's instance id.
        agent_token: Registration token, or `None` to use the dev grant.
        bound_supervisor: Supervisor to store on the instance, or `None`.

    Returns:
        The issued credential.

    Raises:
        ServerError: If the server rejects the grant.
    """
    if agent_token is None:
        body: dict[str, object] = {
            "grant_type": "dev",
            "agent": agent,
            "environment": "dev",
            "instance_id": instance_id,
            "host": socket.gethostname(),
            "runtime": _RUNTIME,
        }
        _bind(body, bound_supervisor)
        response = request(client, "POST", "/v1/token", json_body=body)
    else:
        registered: dict[str, object] = {
            "grant_type": "registration",
            "registration_token": agent_token,
            "instance_id": instance_id,
            "host": socket.gethostname(),
            "runtime": _RUNTIME,
        }
        _bind(registered, bound_supervisor)
        response = request(client, "POST", "/v1/token", json_body=registered)
    return _credential(response)


def refresh(client: httpx.Client, access_token: str) -> RuntimeCredential:
    """Exchange a current runtime JWT for a new one.

    Args:
        client: Open HTTP client.
        access_token: Current runtime JWT.

    Returns:
        The issued credential.

    Raises:
        ServerError: If the server rejects the grant.
        RemoteError: If the server cannot be reached.
    """
    response = request(
        client,
        "POST",
        "/v1/token",
        json_body={"grant_type": "refresh"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return _credential(response)


def _bind(body: dict[str, object], bound_supervisor: str | None) -> None:
    """Add an instance supervisor to a token request.

    Args:
        body: Grant body that will be posted.
        bound_supervisor: Supervisor id, or `None` to leave the body unchanged.
    """
    if bound_supervisor is not None:
        body["bound_supervisor"] = bound_supervisor


def _credential(response: httpx.Response) -> RuntimeCredential:
    """Read a token response.

    Args:
        response: Response from `POST /v1/token`.

    Returns:
        The issued credential.

    Raises:
        ServerError: If the status is not 200 or the body is incomplete.
    """
    if response.status_code != httpx.codes.OK:
        raise ServerError(f"server returned {response.status_code}")
    body = response.json()
    if not isinstance(body, dict):
        raise ServerError("server returned an invalid token response")
    access = body.get("access_token")
    expires_in = body.get("expires_in")
    agent_id = body.get("agent_id")
    agent = body.get("agent")
    environment = body.get("environment")
    if (
        not isinstance(access, str)
        or isinstance(expires_in, bool)
        or not isinstance(expires_in, int)
        or not isinstance(agent_id, str)
        or not isinstance(agent, str)
        or not isinstance(environment, str)
    ):
        raise ServerError("server returned an invalid token response")
    return RuntimeCredential(access, expires_in, agent_id, agent, environment)


__all__ = ["RuntimeCredential", "bootstrap", "refresh", "server_is_dev"]
