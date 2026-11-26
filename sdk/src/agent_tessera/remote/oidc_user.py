"""Exchange an agent runtime JWT for one that acts for a user."""

from __future__ import annotations

import httpx

from agent_tessera.remote.http import request
from agent_tessera.remote.identity import RuntimeCredential, _credential


def exchange_for_user(
    client: httpx.Client,
    *,
    user_token: str,
    actor_token: str,
) -> RuntimeCredential:
    """Ask the control plane to delegate the agent token to a user.

    Args:
        client: Open HTTP client.
        user_token: User access or ID token. This is the subject token.
        actor_token: Current agent runtime JWT.

    Returns:
        The delegated runtime credential.

    Raises:
        ServerError: If the server rejects the exchange.
        RemoteError: If the server cannot be reached.

    Example:
        ```python
        credential = exchange_for_user(
            client,
            user_token=user_token,
            actor_token=actor_token,
        )
        assert credential.access_token
        ```
    """
    response = request(
        client,
        "POST",
        "/v1/token",
        json_body={
            "grant_type": "token-exchange",
            "subject_token": user_token,
            "actor_token": actor_token,
        },
    )
    return _credential(response)


__all__ = ["exchange_for_user"]
