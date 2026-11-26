"""RFC 8693 token exchange for an agent acting for a user."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from sqlalchemy.orm import Session

from tessera_server.api.errors import ApiError
from tessera_server.config import Settings
from tessera_server.db.models import Agent, Instance
from tessera_server.services.keys import verify_runtime_token
from tessera_server.services.oidc import OidcLoginError, open_client, verify_provider_token
from tessera_server.services.registry import _access_token, _require_active

_MESSAGE = "token exchange is not valid"
_GRANTS = frozenset({"token-exchange", "urn:ietf:params:oauth:grant-type:token-exchange"})


def exchange_token(
    session: Session,
    settings: Settings,
    *,
    subject_token: str,
    actor_token: str,
) -> dict[str, object]:
    """Exchange a user token and an agent token for a delegated runtime JWT.

    Args:
        session: Open database session.
        settings: OIDC issuer and client id used to check the user token.
        subject_token: User access or ID token.
        actor_token: Agent runtime JWT. Its `act` claim nests under the new one.

    Returns:
        The token response document. `sub` is the user and `act` is the agent.

    Raises:
        ApiError: Status 401 when either token is rejected, or 403 when the
            agent is not active or the instance is revoked.

    Example:
        ```python
        document = exchange_token(
            session,
            settings,
            subject_token=user_token,
            actor_token=agent_token,
        )
        assert "access_token" in document
        ```
    """
    if not settings.oidc_issuer or not settings.oidc_client_id:
        raise ApiError(401, "invalid_token", _MESSAGE)
    try:
        actor = verify_runtime_token(session, settings, actor_token)
    except ValueError as err:
        raise ApiError(401, "invalid_token", _MESSAGE) from err
    try:
        with open_client() as client:
            user = verify_provider_token(
                settings.oidc_issuer,
                settings.oidc_client_id,
                subject_token,
                client,
            )
    except OidcLoginError as err:
        raise ApiError(401, "invalid_token", _MESSAGE) from err
    agent, instance_id = _actor(session, actor)
    _require_active(agent)
    instance = session.get(Instance, instance_id)
    if instance is None or instance.revoked or instance.agent_id != agent.id:
        raise ApiError(403, "instance_revoked", "instance is revoked")
    return _access_token(
        session,
        settings,
        agent,
        instance_id,
        None,
        subject=_principal(user),
        subject_issuer=settings.oidc_issuer,
        act=_nest(settings, agent, actor.get("act")),
    )


def is_exchange(grant_type: str) -> bool:
    """Report whether a grant type is RFC 8693 token exchange.

    Args:
        grant_type: Value of `grant_type`.

    Returns:
        `True` for `token-exchange` and the URN form.
    """
    return grant_type in _GRANTS


def _actor(session: Session, claims: Mapping[str, object]) -> tuple[Agent, str]:
    """Load the agent and instance named by a runtime token.

    Args:
        session: Open database session.
        claims: Verified runtime claims.

    Returns:
        The agent row and the instance id.

    Raises:
        ApiError: Status 401 when the claims do not name a live instance.
    """
    agent_id = claims.get("agent_id")
    instance_id = claims.get("inst")
    if not isinstance(agent_id, str) or not isinstance(instance_id, str):
        raise ApiError(401, "invalid_token", _MESSAGE)
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise ApiError(401, "invalid_token", _MESSAGE)
    return agent, instance_id


def _principal(claims: Mapping[str, object]) -> str:
    """Format the user subject.

    Args:
        claims: Verified user token.

    Returns:
        `user:` plus the subject when the subject has no colon.

    Raises:
        ApiError: Status 401 when `sub` is missing.
    """
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise ApiError(401, "invalid_token", _MESSAGE)
    if ":" in sub:
        return sub
    return f"user:{sub}"


def _nest(settings: Settings, agent: Agent, inner: object) -> dict[str, object]:
    """Put the current agent outside any actor chain it already carried.

    Args:
        settings: Organization used in the agent URI.
        agent: Agent that is exchanging.
        inner: `act` from the actor token. `None` stops the chain.

    Returns:
        The new `act` object.
    """
    act: dict[str, object] = {
        "sub": f"tessera://{settings.org}/{agent.environment}/{agent.name}",
    }
    if isinstance(inner, dict):
        act["act"] = cast("dict[str, object]", inner)
    return act


__all__ = ["exchange_token", "is_exchange"]
